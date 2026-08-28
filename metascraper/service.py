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


def _noop(_event: Event) -> None:
    pass


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


@dataclass
class OrganizeOutcome:
    result: Optional[organizer.OrganizeResult] = None
    plan: List["organizer.PlannedMove"] = field(default_factory=list)
    dest_root: str = ""
    manifest_path: Optional[str] = None
    dry_run: bool = False
    no_media: bool = False
    failures: List[str] = field(default_factory=list)


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

    for index, path in enumerate(files, start=1):
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

    result.processed = len(files)

    if catalog is not None:
        catalog.save_json(generated_at)
        docx_path = os.path.join(output_dir, f"{opts.master_name}.docx")
        xlsx_path = os.path.join(output_dir, f"{opts.master_name}.xlsx")
        catalog.render_docx(docx_path, generated_at)
        catalog.render_xlsx(xlsx_path, generated_at)
        result.master_outputs = [docx_path, xlsx_path]
        result.summary = catalog.summary()

    report(Event("done", "Catalog complete.", len(files), len(files)))
    return result


# ---------------------------------------------------------------------------
# organize
# ---------------------------------------------------------------------------

def run_organize(
    opts: OrganizeOptions,
    tools: ToolLocation,
    report: Reporter = _noop,
    generated_at: Optional[datetime] = None,
) -> OrganizeOutcome:
    generated_at = generated_at or datetime.now().astimezone()
    folders = opts.folders or ["."]
    dest_root = resolve_output_dir(folders, opts.dest, DEFAULT_ORGANIZE_DIRNAME)
    outcome = OrganizeOutcome(dest_root=dest_root, dry_run=opts.dry_run)

    files = _discover(opts, skip_dirs=[dest_root])
    if not files:
        outcome.no_media = True
        return outcome

    report(Event("phase", f"Reading {len(files)} media file(s).", 0, len(files)))

    infos = []
    probe_failures: List[str] = []
    for index, path in enumerate(files, start=1):
        rel = display_path(path)
        report(Event("item", rel, index, len(files)))
        try:
            infos.append(probe_file(path, tools=tools))
        except Exception as exc:  # noqa: BLE001
            probe_failures.append(f"{rel}: {exc}")

    plan = organizer.plan_moves(infos, dest_root)
    outcome.plan = plan
    outcome.failures = list(probe_failures)

    if opts.dry_run:
        report(Event("done", f"Planned {len(plan)} move(s) (dry run).",
                     len(files), len(files)))
        return outcome

    manifest_path = os.path.join(dest_root, organizer.MANIFEST_NAME)
    manifest = organizer.OrganizeManifest.load(manifest_path, dest_root)

    def on_copy(kind: str, move) -> None:
        if kind in ("copy", "already"):
            report(Event("item", os.path.relpath(move.dest, dest_root)))

    result = organizer.execute_plan(
        plan, manifest, checksum=opts.checksum, generated_at=generated_at,
        on_event=on_copy,
    )
    result.failures.extend(probe_failures)
    result.failed += len(probe_failures)
    outcome.result = result
    outcome.manifest_path = manifest_path
    report(Event("done", f"Copied {result.copied} file(s).", len(files), len(files)))
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
        generated_at=generated_at, on_event=on_delete,
    )
    report(Event("done", "Finalize complete." if not preview else "Preview complete."))
    return outcome


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
