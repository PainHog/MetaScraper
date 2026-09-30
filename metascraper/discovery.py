"""Find media files to catalog inside one or more folders."""

from __future__ import annotations

import os
from typing import Iterable, List, Set

from .models import AUDIO_EXTENSIONS, VIDEO_EXTENSIONS

DEFAULT_MEDIA_EXTENSIONS: Set[str] = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS

# Windows keeps deleted files and restore points in these on every drive.
SYSTEM_DIRS = {"$recycle.bin", "recycler", "system volume information"}


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def find_media_files(
    folders: Iterable[str],
    *,
    recursive: bool = True,
    extra_extensions: Iterable[str] = (),
    all_files: bool = False,
    skip_dirs: Iterable[str] = (),
    skip_hidden: bool = True,
) -> List[str]:
    """Return a de-duplicated, sorted list of media file paths.

    ``skip_dirs`` lets the caller exclude the output directory so generated
    documents are never mistaken for source media.
    """
    extensions = {e.lower() if e.startswith(".") else f".{e.lower()}"
                  for e in extra_extensions}
    extensions |= DEFAULT_MEDIA_EXTENSIONS

    skip_abs = {_norm(d) for d in skip_dirs}
    seen: Set[str] = set()
    results: List[str] = []

    for folder in folders:
        if not os.path.exists(folder):
            continue
        if os.path.isfile(folder):
            _consider(folder, extensions, all_files, seen, results, skip_hidden)
            continue
        for root, dirs, files in os.walk(folder):
            # Prune skipped and hidden directories in-place.
            dirs[:] = [
                d for d in dirs
                if _norm(os.path.join(root, d)) not in skip_abs
                and not (skip_hidden and (d.startswith(".")
                                          or d.lower() in SYSTEM_DIRS))
            ]
            for name in files:
                _consider(
                    os.path.join(root, name),
                    extensions, all_files, seen, results, skip_hidden,
                )
            if not recursive:
                dirs[:] = []

    return sorted(results)


def _consider(path, extensions, all_files, seen, results, skip_hidden) -> None:
    name = os.path.basename(path)
    if skip_hidden and name.startswith("."):
        return
    _, ext = os.path.splitext(name)
    if not all_files and ext.lower() not in extensions:
        return
    key = _norm(path)
    if key in seen:
        return
    seen.add(key)
    results.append(path)
