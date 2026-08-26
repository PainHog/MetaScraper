"""Command-line entry point that ties discovery, probing and reporting together."""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from typing import List, Optional, Sequence

from . import __version__, utils
from .catalog import MasterCatalog
from .discovery import find_media_files
from .probe import ToolLocation, probe_file
from .report import write_report

DEFAULT_OUTPUT_DIRNAME = "MetaScraper_Catalog"
DOCS_SUBDIR = "Documents"
MASTER_BASENAME = "Master_Catalog"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="metascraper",
        description=(
            "Catalog the metadata of camera video and audio recordings: write a "
            "polished Word document for every file and keep a running master "
            "catalog as both .docx and .xlsx."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  metascraper ./Footage\n"
            "  metascraper ./Footage ./Audio -o ./Catalog\n"
            "  metascraper ./Footage --no-recursive --include braw,ari\n"
        ),
    )
    parser.add_argument(
        "folders", nargs="*", default=["."],
        help="One or more folders (or files) to scan. Defaults to the current folder.",
    )
    parser.add_argument(
        "-o", "--output-dir", default=None,
        help="Where to write documents and the master catalog. "
             f"Defaults to a '{DEFAULT_OUTPUT_DIRNAME}' folder beside the input.",
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
        help="Catalog every file regardless of extension.",
    )
    parser.add_argument(
        "--no-per-file", dest="per_file", action="store_false",
        help="Skip the per-file Word documents; only update the master catalog.",
    )
    parser.add_argument(
        "--no-master", dest="master", action="store_false",
        help="Skip the master catalog; only write per-file documents.",
    )
    parser.add_argument(
        "--master-name", default=MASTER_BASENAME,
        help=f"Base name for the master catalog files (default: {MASTER_BASENAME}).",
    )
    parser.add_argument("--ffprobe", default=None, help="Path to the ffprobe binary.")
    parser.add_argument("--exiftool", default=None, help="Path to the exiftool binary.")
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Only print warnings and the summary.",
    )
    parser.add_argument("--version", action="version", version=f"MetaScraper {__version__}")
    return parser


def _slugify(name: str) -> str:
    stem = os.path.splitext(name)[0]
    slug = re.sub(r"[^\w\-. ]+", "_", stem).strip().rstrip(".")
    return slug or "media"


def _unique_doc_path(docs_dir: str, info_name: str, used: set) -> str:
    base = _slugify(info_name)
    candidate = f"{base}.docx"
    counter = 2
    while candidate.lower() in used:
        candidate = f"{base}_{counter}.docx"
        counter += 1
    used.add(candidate.lower())
    return os.path.join(docs_dir, candidate)


def _resolve_output_dir(folders: Sequence[str], explicit: Optional[str]) -> str:
    if explicit:
        return os.path.abspath(explicit)
    first = folders[0]
    if os.path.isfile(first):
        first = os.path.dirname(os.path.abspath(first)) or "."
    return os.path.join(os.path.abspath(first), DEFAULT_OUTPUT_DIRNAME)


def _emit(message: str, quiet: bool = False) -> None:
    if not quiet:
        print(message)


def run(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    generated_at = datetime.now().astimezone()

    folders = args.folders or ["."]
    output_dir = _resolve_output_dir(folders, args.output_dir)
    docs_dir = os.path.join(output_dir, DOCS_SUBDIR)

    tools = ToolLocation(ffprobe=args.ffprobe, exiftool=args.exiftool)
    if not tools.has_ffprobe:
        print(
            "⚠  ffprobe was not found. MetaScraper will still catalog files, but "
            "without FFmpeg installed the technical details (codec, resolution, "
            "duration, etc.) cannot be read.\n"
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

    extra = [e for e in args.include.split(",") if e.strip()]
    files = find_media_files(
        folders,
        recursive=args.recursive,
        extra_extensions=extra,
        all_files=args.all_files,
        skip_dirs=[output_dir],
    )

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

    used_doc_names: set = set()
    new_count = 0
    updated_count = 0
    failures: List[str] = []

    for index, path in enumerate(files, start=1):
        rel = os.path.relpath(path)
        _emit(f"  [{index}/{len(files)}] {rel}", args.quiet)
        try:
            info = probe_file(path, tools=tools)
        except Exception as exc:  # noqa: BLE001 - keep going on a bad file
            failures.append(f"{rel}: {exc}")
            continue

        if args.per_file:
            try:
                doc_path = _unique_doc_path(docs_dir, info.name, used_doc_names)
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

    _print_summary(
        output_dir=output_dir,
        docs_dir=docs_dir if args.per_file else None,
        processed=len(files),
        new_count=new_count,
        updated_count=updated_count,
        master_outputs=master_outputs,
        failures=failures,
        catalog=catalog,
        quiet=args.quiet,
    )
    return 0 if not failures else 2


def _print_summary(*, output_dir, docs_dir, processed, new_count, updated_count,
                   master_outputs, failures, catalog, quiet) -> None:
    print("\n" + "─" * 60)
    print("MetaScraper — done")
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
    if failures:
        print(f"\n  ⚠  {len(failures)} item(s) had problems:")
        for failure in failures[:20]:
            print(f"    - {failure}")
        if len(failures) > 20:
            print(f"    … and {len(failures) - 20} more")
    print("─" * 60)


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
