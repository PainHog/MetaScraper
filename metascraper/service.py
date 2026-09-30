"""Shared engine API used by every front-end (CLI and GUI).

Each task is a single function that takes an options dataclass, a resolved
:class:`~metascraper.probe.ToolLocation`, and a ``report`` callback for progress
events, and returns a structured result. Keeping the orchestration here (instead
of in the CLI) means the GUI drives the exact same code, and adding a new task
later is just another function here plus a thin tab/subcommand on top.
"""

from __future__ import annotations

import hashlib
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional, Sequence

from .catalog import MasterCatalog
from .discovery import find_media_files
from .models import MediaInfo
from .probe import ToolLocation, probe_file
from .report import write_report
from . import organizer

DEFAULT_OUTPUT_DIRNAME = "MetaScraper_Catalog"
DEFAULT_ORGANIZE_DIRNAME = "MetaScraper_Organized"
DOCS_SUBDIR = "Documents"
MASTER_BASENAME = "Master_Catalog"


# ---------------------------------------------------------------------------
# Progress events
# ---------------------------------------------------------------------------

@dataclass
class Event:
    """A progress event emitted while a task runs."""

    kind: str            # "info" | "warn" | "item" | "phase" | "done"
    message: str = ""
    current: Optional[int] = None
    total: Optional[int] = None


Reporter = Callable[[Event], None]
StopCheck = Callable[[], bool]


def _noop(_event: Event) -> None:
    pass


def _never() -> bool:
    return False


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------

@dataclass
class ScanOptions:
    folders: List[str]
    recursive: bool = True
    include: List[str] = field(default_factory=list)
    all_files: bool = False


@dataclass
class CatalogOptions(ScanOptions):
    output_dir: Optional[str] = None
    per_file: bool = True
    master: bool = True
    master_name: str = MASTER_BASENAME


@dataclass
class OrganizeOptions(ScanOptions):
    dest: Optional[str] = None
    dry_run: bool = False
    checksum: bool = False
    # Per-file project assignments (source path -> project names). When set,
    # each file is copied once into every project it's assigned to, and files
    # assigned to none are skipped. None keeps the plain Video|Audio layout.
    projects: Optional[Dict[str, List[str]]] = None
    # Projects every file goes into (the CLI's --project), on top of the above.
    all_projects: List[str] = field(default_factory=list)

    @property
    def uses_projects(self) -> bool:
        return self.projects is not None or bool(self.all_projects)


@dataclass
class FinalizeOptions:
    dest: str
    checksum: bool = False
    apply: bool = False  # actually delete (False = preview only)


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass
class CatalogResult:
    processed: int = 0
    new_count: int = 0
    updated_count: int = 0
    docs_dir: Optional[str] = None
    output_dir: str = ""
    master_outputs: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    summary: Optional[Dict] = None
    no_media: bool = False
    cancelled: bool = False


@dataclass
class ScanResult:
    infos: List[MediaInfo] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    no_media: bool = False
    cancelled: bool = False


@dataclass
class OrganizeOutcome:
    result: Optional[organizer.OrganizeResult] = None
    plan: List["organizer.PlannedMove"] = field(default_factory=list)
    dest_root: str = ""
    manifest_path: Optional[str] = None
    dry_run: bool = False
    no_media: bool = False
    failures: List[str] = field(default_factory=list)
    unassigned: int = 0       # files skipped for having no project
    cancelled: bool = False


@dataclass
class FinalizeOutcome:
    result: Optional[organizer.FinalizeResult] = None
    manifest_path: str = ""
    dest_root: str = ""
    preview: bool = True
    pending: int = 0
    missing_manifest: bool = False


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    stem = os.path.splitext(name)[0]
    slug = re.sub(r"[^\w\-. ]+", "_", stem).strip().rstrip(".")
    return slug or "media"


def plan_doc_names(paths: Sequence[str], docs_dir: str) -> Dict[str, str]:
    """Map each source file's absolute path to a stable per-file .docx path.

    A file's document name depends only on that file (its base name, plus a
    short hash of its absolute path when two files share a base name), so the
    mapping is deterministic across runs and one file's report can never be
    written over another's.
    """
    slugs = [slugify(os.path.basename(p)) for p in paths]
    counts = Counter(slugs)
    mapping: Dict[str, str] = {}
    for path, slug in zip(paths, slugs):
        abspath = os.path.abspath(path)
        if counts[slug] > 1:
            digest = hashlib.sha1(abspath.encode("utf-8")).hexdigest()[:8]
            filename = f"{slug}__{digest}.docx"
        else:
            filename = f"{slug}.docx"
        mapping[abspath] = os.path.join(docs_dir, filename)
    return mapping


def display_path(path: str) -> str:
    """A relative path for display, tolerant of Windows cross-drive paths."""
    try:
        return os.path.relpath(path)
    except ValueError:
        return path


def resolve_output_dir(
    folders: Sequence[str], explicit: Optional[str], default_name: str
) -> str:
    if explicit:
        return os.path.abspath(explicit)
    first = folders[0] if folders else "."
    if os.path.isfile(first):
        first = os.path.dirname(os.path.abspath(first)) or "."
    return os.path.join(os.path.abspath(first), default_name)


def _discover(opts: ScanOptions, skip_dirs: Sequence[str]) -> List[str]:
    return find_media_files(
        opts.folders or ["."],
        recursive=opts.recursive,
        extra_extensions=opts.include,
        all_files=opts.all_files,
        skip_dirs=list(skip_dirs),
    )


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------

def run_catalog(
    opts: CatalogOptions,
    tools: ToolLocation,
    report: Reporter = _noop,
    generated_at: Optional[datetime] = None,
    should_stop: StopCheck = _never,
) -> CatalogResult:
    generated_at = generated_at or datetime.now().astimezone()
    folders = opts.folders or ["."]
    output_dir = resolve_output_dir(folders, opts.output_dir, DEFAULT_OUTPUT_DIRNAME)
    docs_dir = os.path.join(output_dir, DOCS_SUBDIR)
    result = CatalogResult(output_dir=output_dir,
                           docs_dir=docs_dir if opts.per_file else None)

    files = _discover(opts, skip_dirs=[output_dir])
    if not files:
        result.no_media = True
        return result

    report(Event("phase", f"Found {len(files)} media file(s).", 0, len(files)))

    catalog = None
    if opts.master:
        store_path = os.path.join(output_dir, f"{opts.master_name}.json")
        catalog = MasterCatalog(store_path).load()

    doc_names = plan_doc_names(files, docs_dir) if opts.per_file else {}

    done = 0
    for index, path in enumerate(files, start=1):
        if should_stop():
            result.cancelled = True
            break
        done = index
        rel = display_path(path)
        report(Event("item", rel, index, len(files)))
        try:
            info = probe_file(path, tools=tools)
        except Exception as exc:  # noqa: BLE001 - keep going on a bad file
            result.failures.append(f"{rel}: {exc}")
            continue

        if opts.per_file:
            try:
                write_report(info, doc_names[os.path.abspath(path)],
                             generated_at=generated_at)
            except Exception as exc:  # noqa: BLE001
                result.failures.append(f"{rel} (document): {exc}")

        if catalog is not None:
            is_new = catalog.upsert(info, generated_at)
            result.new_count += int(is_new)
            result.updated_count += int(not is_new)

    result.processed = done

    # Save even after a cancel, so the files already read aren't lost.
    if catalog is not None:
        catalog.save_json(generated_at)
        docx_path = os.path.join(output_dir, f"{opts.master_name}.docx")
        xlsx_path = os.path.join(output_dir, f"{opts.master_name}.xlsx")
        catalog.render_docx(docx_path, generated_at)
        catalog.render_xlsx(xlsx_path, generated_at)
        result.master_outputs = [docx_path, xlsx_path]
        result.summary = catalog.summary()

    report(Event("done", "Catalog cancelled." if result.cancelled
                 else "Catalog complete.", done, len(files)))
    return result


# ---------------------------------------------------------------------------
# organize
# ---------------------------------------------------------------------------

def scan_media(
    opts: ScanOptions,
    tools: ToolLocation,
    report: Reporter = _noop,
    skip_dirs: Sequence[str] = (),
    should_stop: StopCheck = _never,
) -> ScanResult:
    """Find and read every media file, without writing anything."""
    result = ScanResult()
    files = _discover(opts, skip_dirs=skip_dirs)
    if not files:
        result.no_media = True
        return result

    report(Event("phase", f"Reading {len(files)} media file(s).", 0, len(files)))
    for index, path in enumerate(files, start=1):
        if should_stop():
            result.cancelled = True
            break
        rel = display_path(path)
        report(Event("item", rel, index, len(files)))
        try:
            result.infos.append(probe_file(path, tools=tools))
        except Exception as exc:  # noqa: BLE001
            result.failures.append(f"{rel}: {exc}")
    return result


def organize_dest(opts: OrganizeOptions) -> str:
    """The library root an organize run with these options writes into."""
    return resolve_output_dir(opts.folders or ["."], opts.dest, DEFAULT_ORGANIZE_DIRNAME)


def _within(path: str, root: str) -> bool:
    path = os.path.normcase(os.path.abspath(path))
    root = os.path.normcase(os.path.abspath(root))
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def run_organize(
    opts: OrganizeOptions,
    tools: ToolLocation,
    report: Reporter = _noop,
    generated_at: Optional[datetime] = None,
    infos: Optional[List[MediaInfo]] = None,
    should_stop: StopCheck = _never,
) -> OrganizeOutcome:
    """Copy recordings into the library.

    Pass ``infos`` (from :func:`scan_media`) to organize files that were already
    read, instead of scanning ``opts.folders`` again.
    """
    generated_at = generated_at or datetime.now().astimezone()
    dest_root = organize_dest(opts)
    outcome = OrganizeOutcome(dest_root=dest_root, dry_run=opts.dry_run)

    if infos is None:
        scan = scan_media(opts, tools, report, skip_dirs=[dest_root],
                          should_stop=should_stop)
        if scan.cancelled:
            outcome.cancelled = True
            return outcome
        infos, probe_failures = scan.infos, scan.failures
        if scan.no_media:
            outcome.no_media = True
            return outcome
    else:
        # Never re-copy files that already live inside the library.
        infos = [info for info in infos if not _within(info.path, dest_root)]
        probe_failures = []
        if not infos:
            outcome.no_media = True
            return outcome

    assignments = None
    if opts.uses_projects:
        chosen = {os.path.abspath(k): v for k, v in (opts.projects or {}).items()}
        assignments = {
            info.path: list(chosen.get(os.path.abspath(info.path), []))
            + list(opts.all_projects)
            for info in infos
        }
        outcome.unassigned = sum(
            1 for names in assignments.values() if not any(n.strip() for n in names))

    plan = organizer.plan_moves(infos, dest_root, projects=assignments)
    outcome.plan = plan
    outcome.failures = list(probe_failures)

    if opts.dry_run:
        report(Event("done", f"Planned {len(plan)} cop{'y' if len(plan) == 1 else 'ies'} "
                     "(dry run).", len(plan), len(plan)))
        return outcome

    manifest_path = os.path.join(dest_root, organizer.MANIFEST_NAME)
    manifest = organizer.OrganizeManifest.load(manifest_path, dest_root)
    report(Event("phase", f"Copying {len(plan)} file(s) into {dest_root}", 0, len(plan)))
    counter = {"n": 0}

    def on_copy(kind: str, move) -> None:
        counter["n"] += 1
        report(Event("item", os.path.relpath(move.dest, dest_root),
                     counter["n"], len(plan)))

    result = organizer.execute_plan(
        plan, manifest, checksum=opts.checksum, generated_at=generated_at,
        on_event=on_copy, should_stop=should_stop,
    )
    result.failures.extend(probe_failures)
    result.failed += len(probe_failures)
    outcome.result = result
    outcome.manifest_path = manifest_path
    outcome.cancelled = result.cancelled
    report(Event("done", ("Organize cancelled — " if result.cancelled else "")
                 + f"copied {result.copied} file(s).", len(plan), len(plan)))
    return outcome


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------

def resolve_manifest(dest: str) -> tuple:
    """Return (manifest_path, dest_root) for a dest that may be a dir or file."""
    target = os.path.abspath(dest)
    if os.path.isfile(target):
        return target, os.path.dirname(target)
    return os.path.join(target, organizer.MANIFEST_NAME), target


def run_finalize(
    opts: FinalizeOptions,
    report: Reporter = _noop,
    generated_at: Optional[datetime] = None,
    should_stop: StopCheck = _never,
) -> FinalizeOutcome:
    generated_at = generated_at or datetime.now().astimezone()
    manifest_path, dest_root = resolve_manifest(opts.dest)
    outcome = FinalizeOutcome(
        manifest_path=manifest_path, dest_root=dest_root, preview=not opts.apply,
    )

    if not os.path.exists(manifest_path):
        outcome.missing_manifest = True
        return outcome

    manifest = organizer.OrganizeManifest.load(manifest_path, dest_root)
    pending = manifest.pending_deletions()
    outcome.pending = len(pending)
    if not pending:
        outcome.result = organizer.FinalizeResult()
        return outcome

    preview = not opts.apply

    def on_delete(kind: str, op) -> None:
        if kind in ("delete", "would-delete"):
            report(Event("item", op.get("source", "")))

    outcome.result = organizer.finalize_moves(
        manifest, checksum=opts.checksum, dry_run=preview,
        generated_at=generated_at, on_event=on_delete, should_stop=should_stop,
    )
    report(Event("done", "Finalize complete." if not preview else "Preview complete."))
    return outcome


def list_projects(dest: Optional[str]) -> List[str]:
    """Projects already present in a library folder (empty if there's none)."""
    if not dest or not os.path.isdir(dest):
        return []
    return organizer.list_projects(dest)


def copied_projects(dest: Optional[str]) -> Dict[str, List[str]]:
    """Source file -> projects it has already been copied into, for a library."""
    if not dest or not os.path.isdir(dest):
        return {}
    return organizer.copied_projects(dest)


# ---------------------------------------------------------------------------
# Tool resolution (shared warning logic)
# ---------------------------------------------------------------------------

def resolve_tools(
    ffprobe: Optional[str] = None, exiftool: Optional[str] = None
) -> ToolLocation:
    return ToolLocation(ffprobe=ffprobe, exiftool=exiftool)


def tool_warnings(tools: ToolLocation, requested_ffprobe=None,
                  requested_exiftool=None) -> List[str]:
    """Human-readable warnings about missing or unusable tools."""
    warnings: List[str] = []
    if requested_ffprobe and not tools.has_ffprobe:
        warnings.append(
            f"The ffprobe path '{requested_ffprobe}' is not a usable executable; "
            "searching your PATH instead."
        )
    if requested_exiftool and not tools.has_exiftool:
        warnings.append(
            f"The exiftool path '{requested_exiftool}' is not a usable executable; "
            "searching your PATH instead."
        )
    if not tools.has_ffprobe:
        warnings.append(
            "ffprobe was not found — install FFmpeg (https://ffmpeg.org/download.html) "
            "for full technical metadata (codec, resolution, duration, recording date)."
        )
    if not tools.has_exiftool:
        warnings.append(
            "exiftool not found — camera make/model and GPS rely on container tags "
            "only. Install ExifTool for richer camera details."
        )
    return warnings
