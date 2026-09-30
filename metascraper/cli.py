"""Command-line front-end. All real work lives in :mod:`metascraper.service`.

MetaScraper has three subcommands:

* ``catalog``  — write per-file Word documents and update the master catalog.
* ``organize`` — copy recordings into a tidy ``Video|Audio/Camera/Date`` tree,
  optionally inside one or more project folders.
* ``finalize`` — delete the originals whose copies verify against the manifest.

For backward compatibility, ``metascraper <folders>`` with no subcommand runs
``catalog``.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from . import __version__, organizer, service, utils
from .service import (
    CatalogOptions,
    FinalizeOptions,
    OrganizeOptions,
    Event,
)

SUBCOMMANDS = {"catalog", "organize", "finalize"}

# Backwards-compatible aliases (referenced by tests and older docs).
_slugify = service.slugify
_plan_doc_names = service.plan_doc_names
_display_path = service.display_path


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _add_scan_args(parser: argparse.ArgumentParser) -> None:
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
        description="Catalog and organize the metadata of camera video and audio "
                    "recordings.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  metascraper ./Footage                       (catalog: the default)\n"
            "  metascraper catalog ./Footage ./Audio -o ./Catalog\n"
            "  metascraper organize ./Footage -o ./Library\n"
            "  metascraper organize ./Footage -o ./Library --project \"Wildlife Doc\"\n"
            "  metascraper finalize ./Library --yes        (delete verified originals)\n"
        ),
    )
    parser.add_argument("--version", action="version", version=f"MetaScraper {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    cat = subparsers.add_parser(
        "catalog", help="Write per-file Word documents and update the master catalog.",
        description="Write a per-file Word document and update the master catalog.",
    )
    _add_scan_args(cat)
    cat.add_argument(
        "-o", "--output-dir", default=None,
        help="Where to write documents and the master catalog. "
             f"Defaults to a '{service.DEFAULT_OUTPUT_DIRNAME}' folder beside the input.",
    )
    cat.add_argument("--no-per-file", dest="per_file", action="store_false",
                     help="Skip the per-file Word documents; only update the master.")
    cat.add_argument("--no-master", dest="master", action="store_false",
                     help="Skip the master catalog; only write per-file documents.")
    cat.add_argument("--master-name", default=service.MASTER_BASENAME,
                     help=f"Base name for the master files (default: {service.MASTER_BASENAME}).")
    cat.set_defaults(func=_run_catalog)

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
             f"Defaults to a '{service.DEFAULT_ORGANIZE_DIRNAME}' folder beside the input.",
    )
    org.add_argument("--dry-run", action="store_true",
                     help="Show the planned moves without copying anything.")
    org.add_argument("--checksum", action="store_true",
                     help="Verify copies with a SHA-256 checksum (slower, strongest).")
    org.add_argument(
        "-p", "--project", dest="projects", action="append", default=[],
        metavar="NAME",
        help="Copy every file into <dest>/NAME/Video|Audio/... instead. Repeat to "
             "copy into several projects (one copy each).",
    )
    org.set_defaults(func=_run_organize)

    fin = subparsers.add_parser(
        "finalize", help="Delete the originals whose copies verify against the manifest.",
        description=(
            "The 'move later' step: delete each original that its organized copy "
            "still verifies against. Nothing is deleted unless it verifies."
        ),
    )
    fin.add_argument("dest", help="The organized destination root (or a manifest .json file).")
    fin.add_argument("--checksum", action="store_true",
                     help="Re-verify with a SHA-256 checksum before deleting (recommended).")
    fin.add_argument("--dry-run", action="store_true",
                     help="Show what would be deleted without deleting anything.")
    fin.add_argument("-y", "--yes", action="store_true",
                     help="Actually delete. Without this, finalize only previews.")
    fin.add_argument("-q", "--quiet", action="store_true")
    fin.set_defaults(func=_run_finalize)

    return parser


# ---------------------------------------------------------------------------
# Console output that never crashes
# ---------------------------------------------------------------------------

def _prepare_output() -> None:
    """Make printing safe whatever the output encoding.

    When output is redirected on Windows (to a file, a pipe, a script) Python
    writes cp1252, which can't encode characters like ─ ⚠ →; printing one
    used to crash the command after its work was done. Such characters now
    come out as '?' (and the ones below pick a plain fallback instead).
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(errors="replace")
            except (ValueError, OSError):
                pass


def _glyph(fancy: str, plain: str, stream=None) -> str:
    """``fancy`` if the output can show it, else ``plain``."""
    stream = stream if stream is not None else sys.stdout
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        fancy.encode(encoding)
        return fancy
    except (UnicodeEncodeError, LookupError):
        return plain


def _rule() -> str:
    return _glyph("─", "-") * 60


# ---------------------------------------------------------------------------
# Progress reporting → console
# ---------------------------------------------------------------------------

def _make_reporter(quiet: bool):
    def report(event: Event) -> None:
        if event.kind == "warn":
            print(_glyph("⚠", "!", sys.stderr) + "  " + event.message, file=sys.stderr)
            return
        if quiet:
            return
        if event.kind == "phase":
            print(event.message)
        elif event.kind == "item":
            if event.current and event.total:
                print(f"  [{event.current}/{event.total}] {event.message}")
            else:
                print(f"    • {event.message}")
    return report


def _split_include(value: str) -> List[str]:
    return [e.strip() for e in value.split(",") if e.strip()]


def _resolve_tools(args):
    tools = service.resolve_tools(args.ffprobe, args.exiftool)
    for warning in service.tool_warnings(tools, args.ffprobe, args.exiftool):
        print(_glyph("⚠", "!", sys.stderr) + "  " + warning, file=sys.stderr)
    return tools


# ---------------------------------------------------------------------------
# catalog
# ---------------------------------------------------------------------------

def _run_catalog(args) -> int:
    opts = CatalogOptions(
        folders=args.folders or ["."], recursive=args.recursive,
        include=_split_include(args.include), all_files=args.all_files,
        output_dir=args.output_dir, per_file=args.per_file,
        master=args.master, master_name=args.master_name,
    )
    tools = _resolve_tools(args)
    result = service.run_catalog(opts, tools, report=_make_reporter(args.quiet))

    if result.no_media:
        print("No media files were found. Checked: "
              + ", ".join(os.path.abspath(f) for f in opts.folders))
        return 1

    print("\n" + _rule())
    print("MetaScraper — catalog done")
    print(_rule())
    print(f"  Files processed : {result.processed}")
    if result.docs_dir:
        print(f"  Per-file docs   : {result.docs_dir}")
    if result.summary is not None:
        print(f"  Master catalog  : {result.new_count} new, "
              f"{result.updated_count} updated, {result.summary['count']} total")
        print(f"  Total duration  : {utils.human_duration(result.summary['total_seconds'])}")
        print(f"  Total size      : {utils.human_file_size(result.summary['total_bytes'])}")
        for output in result.master_outputs:
            print(f"    • {output}")
    _print_failures(result.failures)
    print(_rule())
    return 0 if not result.failures else 2


# ---------------------------------------------------------------------------
# organize
# ---------------------------------------------------------------------------

def _run_organize(args) -> int:
    for name in args.projects:
        problem = organizer.project_name_problem(name)
        if problem:
            print(f"Invalid project name '{name}': {problem}", file=sys.stderr)
            return 1
    opts = OrganizeOptions(
        folders=args.folders or ["."], recursive=args.recursive,
        include=_split_include(args.include), all_files=args.all_files,
        dest=args.dest, dry_run=args.dry_run, checksum=args.checksum,
        all_projects=[name.strip() for name in args.projects],
    )
    tools = _resolve_tools(args)
    outcome = service.run_organize(opts, tools, report=_make_reporter(args.quiet))

    if outcome.no_media:
        print("No media files were found. Checked: "
              + ", ".join(os.path.abspath(f) for f in opts.folders))
        return 1

    if outcome.cancelled:
        print("Organize was cancelled.", file=sys.stderr)
        return 2

    if outcome.dry_run:
        _print_organize_plan(outcome.plan, outcome.dest_root, outcome.failures)
        return 0

    result = outcome.result
    print("\n" + _rule())
    print("MetaScraper — organize done")
    print(_rule())
    print(f"  Copied          : {result.copied}"
          + (f"  ({result.renamed} renamed to avoid collisions)" if result.renamed else ""))
    print(f"  Already in place : {result.skipped}")
    print(f"  Destination     : {outcome.dest_root}")
    print(f"  Manifest        : {outcome.manifest_path}")
    _print_failures(result.failures, label="could not be copied")
    print("\n  Originals are untouched. When you've verified the copies, run:")
    print(f"    metascraper finalize \"{outcome.dest_root}\" --yes")
    print(_rule())
    return 0 if not result.failures else 2


def _print_organize_plan(plan, dest_root, failures) -> None:
    print("\n" + _rule())
    print(f"MetaScraper — organize (dry run): {len(plan)} cop"
          + ("y" if len(plan) == 1 else "ies"))
    print(_rule())
    for move in plan[:200]:
        rel = os.path.relpath(move.dest, dest_root)
        arrow = _glyph("→", "->")
        marker = {"copy": arrow, "skip-identical": "=",
                  "collision-renamed": arrow + "*"}.get(move.action, arrow)
        print(f"  {marker} {rel}")
    if len(plan) > 200:
        print(f"    … and {len(plan) - 200} more")
    print("\n  Nothing was copied (dry run). Re-run without --dry-run to copy.")
    _print_failures(failures)
    print(_rule())


# ---------------------------------------------------------------------------
# finalize
# ---------------------------------------------------------------------------

def _run_finalize(args) -> int:
    opts = FinalizeOptions(
        dest=args.dest, checksum=args.checksum,
        apply=(args.yes and not args.dry_run),
    )
    outcome = service.run_finalize(opts, report=_make_reporter(args.quiet))

    if outcome.missing_manifest:
        print(
            f"No organize manifest found at {outcome.manifest_path}.\n"
            "Run 'metascraper organize' first, or point finalize at the manifest.",
            file=sys.stderr,
        )
        return 1

    if outcome.pending == 0:
        print("Nothing to finalize — no copied originals are awaiting deletion.")
        return 0

    result = outcome.result
    preview = outcome.preview
    print("\n" + _rule())
    print("MetaScraper — finalize " + ("(preview)" if preview else "done"))
    print(_rule())
    verb = "Would delete" if preview else "Deleted"
    freed = "Would free" if preview else "Freed"
    print(f"  {verb:15}: {result.deleted} original(s)")
    print(f"  {freed:15}: {utils.human_file_size(result.freed_bytes)}")
    if result.skipped:
        print(f"  Already gone    : {result.skipped}")
    _print_failures(result.problems, label="kept (did not verify)")
    if preview and not args.dry_run:
        print("\n  This was a preview. Re-run with --yes to delete the originals:")
        print(f"    metascraper finalize \"{args.dest}\" --yes"
              + (" --checksum" if args.checksum else ""))
    print(_rule())
    return 0 if not result.problems else 2


# ---------------------------------------------------------------------------
# Shared output + dispatch
# ---------------------------------------------------------------------------

def _print_failures(failures, label: str = "had problems") -> None:
    if not failures:
        return
    print(f"\n  {_glyph('⚠', '!')}  {len(failures)} item(s) {label}:")
    for failure in failures[:20]:
        print(f"    - {failure}")
    if len(failures) > 20:
        print(f"    … and {len(failures) - 20} more")


def run(argv: Optional[List[str]] = None) -> int:
    _prepare_output()
    argv = list(sys.argv[1:] if argv is None else argv)
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
