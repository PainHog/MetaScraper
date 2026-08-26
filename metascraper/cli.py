"""Command-line entry point.

MetaScraper has three subcommands:

* ``catalog``  — write per-file Word documents and update the master catalog.
* ``organize`` — copy recordings into a tidy ``Video|Audio/Camera/Date`` tree
  (originals untouched) and record a manifest.
* ``finalize`` — delete the originals whose copies verify against the manifest.

For backward compatibility, ``metascraper <folders>`` with no subcommand runs
``catalog``.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
from collections import Counter
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from . import __version__, organizer, utils
from .catalog import MasterCatalog
from .discovery import find_media_files
from .probe import ToolLocation, probe_file
from .report import write_report

DEFAULT_OUTPUT_DIRNAME = "MetaScraper_Catalog"
DEFAULT_ORGANIZE_DIRNAME = "MetaScraper_Organized"
DOCS_SUBDIR = "Documents"
MASTER_BASENAME = "Master_Catalog"
SUBCOMMANDS = {"catalog", "organize", "finalize"}


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _add_scan_args(parser: argparse.ArgumentParser) -> None:
    """Args shared by commands that discover and probe media."""
    parser.add_argument(
        "folders", nargs="*", default=["."],
        help="One or more folders (or files) to scan. Defaults to the current folder.",
    )
    parser.add_argument(
        "--no-recursive", dest="recursive", action="store_false",
        help="Do not descend into subfolders.",
    )
    parser.add_argument(
        "--include", default="",
        help="Comma-separated extra file extensions to treat as media (e.g. braw,ari).",
    )
    parser.add_argument(
        "--all-files", action="store_true",
        help="Process every file regardless of extension.",
    )
    parser.add_argument("--ffprobe", default=None, help="Path to the ffprobe binary.")
    parser.add_argument("--exiftool", default=None, help="Path to the exiftool binary.")
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Only print warnings and the summary.",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="metascraper",
        description=(
            "Catalog and organize the metadata of camera video and audio "
            "recordings."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  metascraper ./Footage                       (catalog: the default)\n"
            "  metascraper catalog ./Footage ./Audio -o ./Catalog\n"
            "  metascraper organize ./Footage -o ./Library\n"
            "  metascraper finalize ./Library              (delete verified originals)\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"MetaScraper {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    # -- catalog ----------------------------------------------------------
    cat = subparsers.add_parser(
        "catalog",
        help="Write per-file Word documents and update the master catalog.",
        description="Write a per-file Word document and update the master catalog.",
    )
    _add_scan_args(cat)
    cat.add_argument(
        "-o", "--output-dir", default=None,
        help="Where to write documents and the master catalog. "
             f"Defaults to a '{DEFAULT_OUTPUT_DIRNAME}' folder beside the input.",
    )
    cat.add_argument(
        "--no-per-file", dest="per_file", action="store_false",
        help="Skip the per-file Word documents; only update the master catalog.",
    )
    cat.add_argument(
        "--no-master", dest="master", action="store_false",
        help="Skip the master catalog; only write per-file documents.",
    )
    cat.add_argument(
        "--master-name", default=MASTER_BASENAME,
        help=f"Base name for the master catalog files (default: {MASTER_BASENAME}).",
    )
    cat.set_defaults(func=_run_catalog)

    # -- organize ---------------------------------------------------------
    org = subparsers.add_parser(
        "organize",
        help="Copy recordings into a Video|Audio/Camera/Date tree (originals kept).",
        description=(
            "Copy each recording into <dest>/Video|Audio/<Camera>/<YYYY-MM-DD>/. "
            "Originals are never touched; run 'finalize' afterwards to remove them."
        ),
    )
    _add_scan_args(org)
    org.add_argument(
        "-o", "--dest", default=None,
        help="Destination root for the organized tree. "
             f"Defaults to a '{DEFAULT_ORGANIZE_DIRNAME}' folder beside the input.",
    )
    org.add_argument(
        "--dry-run", action="store_true",
        help="Show the planned moves without copying anything.",
    )
    org.add_argument(
        "--checksum", action="store_true",
        help="Verify copies with a SHA-256 checksum (slower, strongest guarantee).",
    )
    org.set_defaults(func=_run_organize)

    # -- finalize ---------------------------------------------------------
    fin = subparsers.add_parser(
        "finalize",
        help="Delete the originals whose copies verify against the manifest.",
        description=(
            "The 'move later' step: delete each original that its organized copy "
            "still verifies against. Nothing is deleted unless it verifies."
        ),
    )
    fin.add_argument(
        "dest",
        help="The organized destination root (or a manifest .json file).",
    )
    fin.add_argument(
        "--checksum", action="store_true",
        help="Re-verify with a SHA-256 checksum before deleting (recommended).",
    )
    fin.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be deleted without deleting anything.",
    )
    fin.add_argument(
        "-y", "--yes", action="store_true",
        help="Actually delete. Without this, finalize only previews.",
    )
    fin.add_argument("-q", "--quiet", action="store_true")
    fin.set_defaults(func=_run_finalize)

    return parser


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    stem = os.path.splitext(name)[0]
    slug = re.sub(r"[^\w\-. ]+", "_", stem).strip().rstrip(".")
    return slug or "media"


def _plan_doc_names(paths: Sequence[str], docs_dir: str) -> Dict[str, str]:
    """Map each source file's absolute path to a stable per-file .docx path.

    A file's document name depends only on that file (its base name, plus a
    short hash of its absolute path when two files share a base name), so the
    mapping is deterministic across runs and one file's report can never be
    written over another's.
    """
    slugs = [_slugify(os.path.basename(p)) for p in paths]
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


def _display_path(path: str) -> str:
    """A relative path for display, tolerant of Windows cross-drive paths."""
    try:
        return os.path.relpath(path)
    except ValueError:
        # os.path.relpath raises when path and cwd are on different drives.
        return path


def _resolve_output_dir(
    folders: Sequence[str], explicit: Optional[str], default_name: str
) -> str:
    if explicit:
        return os.path.abspath(explicit)
    first = folders[0]
    if os.path.isfile(first):
        first = os.path.dirname(os.path.abspath(first)) or "."
    return os.path.join(os.path.abspath(first), default_name)


def _emit(message: str, quiet: bool = False) -> None:
    if not quiet:
        print(message)


def _resolve_tools(args) -> ToolLocation:
    """Locate ffprobe/exiftool and warn about missing or bad paths."""
    tools = ToolLocation(ffprobe=args.ffprobe, exiftool=args.exiftool)
    if args.ffprobe and not tools.has_ffprobe:
        print(
            f"⚠  The --ffprobe path '{args.ffprobe}' is not a usable executable; "
            "falling back to searching your PATH.",
            file=sys.stderr,
        )
    if args.exiftool and not tools.has_exiftool:
        print(
            f"⚠  The --exiftool path '{args.exiftool}' is not a usable executable; "
            "falling back to searching your PATH.",
            file=sys.stderr,
        )
    if not tools.has_ffprobe:
        print(
            "⚠  ffprobe was not found. MetaScraper will still run, but without "
            "FFmpeg the technical details (codec, resolution, duration, recording "
            "date) cannot be read.\n"
            "   Install FFmpeg from https://ffmpeg.org/download.html and re-run, "
            "or pass --ffprobe /path/to/ffprobe.",
            file=sys.stderr,
        )
    if not tools.has_exiftool:
        _emit(
            "ℹ  exiftool not found — camera make/model and GPS will rely on "
            "container tags only. Install ExifTool for richer camera details.",
            args.quiet,
        )
    return tools


def _discover(args, skip_dirs: Sequence[str]) -> List[str]:
    extra = [e.strip() for e in args.include.split(",") if e.strip()]
    return find_media_files(
        args.folders or ["."],
        recursive=args.recursive,
        extra_extensions=extra,
        all_files=args.all_files,
        skip_dirs=list(skip_dirs),
    )


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------

def _run_catalog(args) -> int:
    generated_at = datetime.now().astimezone()
    folders = args.folders or ["."]
    output_dir = _resolve_output_dir(folders, args.output_dir, DEFAULT_OUTPUT_DIRNAME)
    docs_dir = os.path.join(output_dir, DOCS_SUBDIR)

    tools = _resolve_tools(args)
    files = _discover(args, skip_dirs=[output_dir])

    if not files:
        print(
            "No media files were found. Checked: "
            + ", ".join(os.path.abspath(f) for f in folders)
        )
        return 1

    _emit(f"Found {len(files)} media file(s). Output → {output_dir}", args.quiet)

    catalog = None
    if args.master:
        store_path = os.path.join(output_dir, f"{args.master_name}.json")
        catalog = MasterCatalog(store_path).load()

    doc_names = _plan_doc_names(files, docs_dir) if args.per_file else {}
    new_count = 0
    updated_count = 0
    failures: List[str] = []

    for index, path in enumerate(files, start=1):
        rel = _display_path(path)
        _emit(f"  [{index}/{len(files)}] {rel}", args.quiet)
        try:
            info = probe_file(path, tools=tools)
        except Exception as exc:  # noqa: BLE001 - keep going on a bad file
            failures.append(f"{rel}: {exc}")
            continue

        if args.per_file:
            try:
                doc_path = doc_names[os.path.abspath(path)]
                write_report(info, doc_path, generated_at=generated_at)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{rel} (document): {exc}")

        if catalog is not None:
            is_new = catalog.upsert(info, generated_at)
            new_count += int(is_new)
            updated_count += int(not is_new)

    master_outputs: List[str] = []
    if catalog is not None:
        catalog.save_json(generated_at)
        docx_path = os.path.join(output_dir, f"{args.master_name}.docx")
        xlsx_path = os.path.join(output_dir, f"{args.master_name}.xlsx")
        catalog.render_docx(docx_path, generated_at)
        catalog.render_xlsx(xlsx_path, generated_at)
        master_outputs = [docx_path, xlsx_path]

    _print_catalog_summary(
        docs_dir=docs_dir if args.per_file else None,
        processed=len(files),
        new_count=new_count,
        updated_count=updated_count,
        master_outputs=master_outputs,
        failures=failures,
        catalog=catalog,
    )
    return 0 if not failures else 2


def _print_catalog_summary(*, docs_dir, processed, new_count, updated_count,
                           master_outputs, failures, catalog) -> None:
    print("\n" + "─" * 60)
    print("MetaScraper — catalog done")
    print("─" * 60)
    print(f"  Files processed : {processed}")
    if docs_dir:
        print(f"  Per-file docs   : {docs_dir}")
    if catalog is not None:
        summary = catalog.summary()
        print(f"  Master catalog  : {new_count} new, {updated_count} updated, "
              f"{summary['count']} total")
        print(f"  Total duration  : {utils.human_duration(summary['total_seconds'])}")
        print(f"  Total size      : {utils.human_file_size(summary['total_bytes'])}")
        for output in master_outputs:
            print(f"    • {output}")
    _print_failures(failures)
    print("─" * 60)


# ---------------------------------------------------------------------------
# organize
# ---------------------------------------------------------------------------

def _run_organize(args) -> int:
    generated_at = datetime.now().astimezone()
    folders = args.folders or ["."]
    dest_root = _resolve_output_dir(folders, args.dest, DEFAULT_ORGANIZE_DIRNAME)

    tools = _resolve_tools(args)
    files = _discover(args, skip_dirs=[dest_root])

    if not files:
        print(
            "No media files were found. Checked: "
            + ", ".join(os.path.abspath(f) for f in folders)
        )
        return 1

    verb = "Planning" if args.dry_run else "Organizing"
    _emit(f"{verb} {len(files)} media file(s) → {dest_root}", args.quiet)

    infos = []
    probe_failures: List[str] = []
    for index, path in enumerate(files, start=1):
        rel = _display_path(path)
        _emit(f"  reading [{index}/{len(files)}] {rel}", args.quiet)
        try:
            infos.append(probe_file(path, tools=tools))
        except Exception as exc:  # noqa: BLE001
            probe_failures.append(f"{rel}: {exc}")

    plan = organizer.plan_moves(infos, dest_root)

    if args.dry_run:
        _print_organize_plan(plan, dest_root, probe_failures)
        return 0

    manifest_path = os.path.join(dest_root, organizer.MANIFEST_NAME)
    manifest = organizer.OrganizeManifest.load(manifest_path, dest_root)
    result = organizer.execute_plan(
        plan, manifest, checksum=args.checksum, generated_at=generated_at,
    )
    result.failures.extend(probe_failures)
    result.failed += len(probe_failures)
    _print_organize_summary(result, dest_root, manifest_path)
    return 0 if not result.failures else 2


def _print_organize_plan(plan, dest_root, probe_failures) -> None:
    print("\n" + "─" * 60)
    print(f"MetaScraper — organize (dry run): {len(plan)} file(s)")
    print("─" * 60)
    for move in plan[:200]:
        rel = os.path.relpath(move.dest, dest_root)
        marker = {"copy": "→", "skip-identical": "=", "collision-renamed": "→*"}.get(
            move.action, "→"
        )
        print(f"  {marker} {rel}")
    if len(plan) > 200:
        print(f"    … and {len(plan) - 200} more")
    print("\n  Nothing was copied (dry run). Re-run without --dry-run to copy.")
    _print_failures(probe_failures)
    print("─" * 60)


def _print_organize_summary(result, dest_root, manifest_path) -> None:
    print("\n" + "─" * 60)
    print("MetaScraper — organize done")
    print("─" * 60)
    print(f"  Copied          : {result.copied}"
          + (f"  ({result.renamed} renamed to avoid collisions)" if result.renamed else ""))
    print(f"  Already in place : {result.skipped}")
    print(f"  Destination     : {dest_root}")
    print(f"  Manifest        : {manifest_path}")
    _print_failures(result.failures, label="could not be copied")
    print("\n  Originals are untouched. When you've verified the copies, run:")
    print(f"    metascraper finalize \"{dest_root}\" --yes")
    print("─" * 60)


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------

def _run_finalize(args) -> int:
    generated_at = datetime.now().astimezone()
    target = os.path.abspath(args.dest)
    if os.path.isfile(target):
        manifest_path = target
        dest_root = os.path.dirname(target)
    else:
        manifest_path = os.path.join(target, organizer.MANIFEST_NAME)
        dest_root = target

    if not os.path.exists(manifest_path):
        print(
            f"No organize manifest found at {manifest_path}.\n"
            "Run 'metascraper organize' first, or point finalize at the manifest.",
            file=sys.stderr,
        )
        return 1

    manifest = organizer.OrganizeManifest.load(manifest_path, dest_root)
    pending = manifest.pending_deletions()
    if not pending:
        print("Nothing to finalize — no copied originals are awaiting deletion.")
        return 0

    # Without --yes (and not an explicit --dry-run) we only preview, so a
    # destructive delete never happens by accident.
    preview = args.dry_run or not args.yes
    result = organizer.finalize_moves(
        manifest, checksum=args.checksum, dry_run=preview, generated_at=generated_at,
    )

    print("\n" + "─" * 60)
    print("MetaScraper — finalize " + ("(preview)" if preview else "done"))
    print("─" * 60)
    if preview:
        print(f"  Would delete    : {result.deleted} original(s)")
        print(f"  Would free      : {utils.human_file_size(result.freed_bytes)}")
    else:
        print(f"  Deleted         : {result.deleted} original(s)")
        print(f"  Freed           : {utils.human_file_size(result.freed_bytes)}")
    if result.skipped:
        print(f"  Already gone    : {result.skipped}")
    _print_failures(result.problems, label="kept (did not verify)")
    if preview and not args.dry_run:
        print("\n  This was a preview. Re-run with --yes to delete the originals:")
        print(f"    metascraper finalize \"{args.dest}\" --yes"
              + (" --checksum" if args.checksum else ""))
    print("─" * 60)
    return 0 if not result.problems else 2


# ---------------------------------------------------------------------------
# Shared output + dispatch
# ---------------------------------------------------------------------------

def _print_failures(failures, label: str = "had problems") -> None:
    if not failures:
        return
    print(f"\n  ⚠  {len(failures)} item(s) {label}:")
    for failure in failures[:20]:
        print(f"    - {failure}")
    if len(failures) > 20:
        print(f"    … and {len(failures) - 20} more")


def run(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Backward compatibility: default to the 'catalog' subcommand when the first
    # token is neither a known subcommand nor a top-level flag.
    if not argv:
        argv = ["catalog"]
    elif argv[0] not in SUBCOMMANDS and argv[0] not in ("-h", "--help", "--version"):
        argv = ["catalog"] + argv

    args = build_parser().parse_args(argv)
    if not getattr(args, "func", None):
        build_parser().print_help()
        return 1
    return args.func(args)


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
