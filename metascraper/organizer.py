"""Organize recordings into a tidy tree, copy-first then move-later.

The layout is::

    <dest>/
      Video/<Camera>/<YYYY-MM-DD>/<original file>
      Audio/<Camera>/<YYYY-MM-DD>/<original file>

or, when files are assigned to projects, one copy per project::

    <dest>/<Project>/Video|Audio/<Camera>/<YYYY-MM-DD>/<original file>

Values come from the metadata MetaScraper already extracts. The flow is
deliberately two-step and non-destructive:

1. :func:`execute_plan` copies each file into the tree, verifies the copy, and
   records every operation in a manifest. The originals are never touched.
2. :func:`finalize_moves` later deletes only the originals whose copies
   re-verify against the manifest — every copy, when a file went to several
   projects.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from .models import MediaInfo

MANIFEST_NAME = ".metascraper_organize_manifest.json"
MANIFEST_SCHEMA = 1

# Characters that are illegal or troublesome in path components across OSes.
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}

UNKNOWN_CAMERA = "Unknown Camera"
UNKNOWN_DATE = "Unknown Date"

# Top-level folders of the plain (project-less) layout. A project may not use
# one of these names, or it would be indistinguishable from that layout.
KIND_FOLDERS = ("Video", "Audio", "Other")


def sanitize_component(name: str, fallback: str = "Untitled") -> str:
    """Make a string safe to use as a single folder or file name."""
    if not name:
        return fallback
    cleaned = _ILLEGAL.sub("_", str(name))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    # Windows dislikes trailing dots/spaces.
    cleaned = cleaned.rstrip(". ")
    if not cleaned:
        return fallback
    if cleaned.lower() in _WINDOWS_RESERVED:
        cleaned = f"{cleaned}_"
    # Keep individual components to a sane length.
    if len(cleaned) > 120:
        cleaned = cleaned[:120].rstrip(". ")
    return cleaned or fallback


def camera_label(info: MediaInfo) -> str:
    """Build a folder-friendly camera/device label from make + model."""
    make = (info.camera_make or "").strip()
    model = (info.camera_model or "").strip()
    if make and model:
        # Avoid "Sony Sony A7" when the make is already part of the model.
        label = model if make.lower() in model.lower() else f"{make} {model}"
    else:
        label = model or make or UNKNOWN_CAMERA
    return sanitize_component(label, fallback=UNKNOWN_CAMERA)


def date_label(info: MediaInfo) -> str:
    """Return the YYYY-MM-DD folder name, preferring the recording date."""
    stamp = info.recorded_at or info.fs_modified or info.fs_created
    if isinstance(stamp, datetime):
        return stamp.strftime("%Y-%m-%d")
    return UNKNOWN_DATE


def top_folder(info: MediaInfo) -> str:
    """Map media kind to the top-level folder."""
    if info.media_kind in ("Video", "Audio"):
        return info.media_kind
    return "Other"


def dest_relpath(info: MediaInfo, project: Optional[str] = None) -> str:
    """Relative destination path (folders + original file name) for a file."""
    parts = [project_folder(project)] if project else []
    parts += [
        top_folder(info),
        camera_label(info),
        date_label(info),
        sanitize_component(info.name, fallback="media"),
    ]
    return os.path.join(*parts)


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

def project_folder(name: str) -> str:
    """The folder name a project's copies go into."""
    return sanitize_component(name, fallback="Untitled Project")


def project_name_problem(name: str) -> Optional[str]:
    """Why ``name`` can't be used as a project, or None if it's fine."""
    stripped = (name or "").strip()
    if not stripped:
        return "Enter a project name."
    if stripped.lower() in (k.lower() for k in KIND_FOLDERS):
        return f"'{stripped}' is reserved for the library's own folders."
    if project_folder(stripped) != stripped:
        return ("That name can't be used as a folder name. Avoid "
                '< > : " / \\ | ? * and a trailing dot or space.')
    return None


def _unique_projects(names: Iterable[str]) -> List[str]:
    """Drop blanks and names that map to the same folder (case-insensitively,
    since Windows folders are), keeping the first spelling."""
    seen: Set[str] = set()
    unique: List[str] = []
    for name in names:
        if not name or not str(name).strip():
            continue
        key = project_folder(str(name).strip()).lower()
        if key not in seen:
            seen.add(key)
            unique.append(str(name).strip())
    return unique


def list_projects(dest_root: str) -> List[str]:
    """Projects already present in a library.

    A project is any folder recorded as one in the manifest, or any top-level
    folder that holds a ``Video``/``Audio``/``Other`` tree.
    """
    dest_root = os.path.abspath(dest_root)
    found: List[str] = []
    manifest_path = os.path.join(dest_root, MANIFEST_NAME)
    if os.path.exists(manifest_path):
        manifest = OrganizeManifest.load(manifest_path, dest_root)
        found += [op["project"] for op in manifest.operations.values()
                  if op.get("project")]
    try:
        entries = sorted(os.scandir(dest_root), key=lambda e: e.name.lower())
    except OSError:
        entries = []
    for entry in entries:
        if entry.name.startswith(".") or entry.name in KIND_FOLDERS:
            continue
        try:
            if entry.is_dir() and any(
                os.path.isdir(os.path.join(entry.path, kind)) for kind in KIND_FOLDERS
            ):
                found.append(entry.name)
        except OSError:
            continue
    return _unique_projects(found)


def copied_projects(dest_root: str) -> Dict[str, List[str]]:
    """Map each source file to the projects it has already been copied into."""
    dest_root = os.path.abspath(dest_root)
    manifest_path = os.path.join(dest_root, MANIFEST_NAME)
    result: Dict[str, List[str]] = {}
    if not os.path.exists(manifest_path):
        return result
    manifest = OrganizeManifest.load(manifest_path, dest_root)
    for op in manifest.operations.values():
        if op.get("status") == "copied" and op.get("project"):
            projects = result.setdefault(_source_key(op["source"]), [])
            if op["project"] not in projects:
                projects.append(op["project"])
    return result


def _source_key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

@dataclass
class PlannedMove:
    source: str
    dest: str
    media_kind: str
    camera: str
    date: str
    size: Optional[int]
    action: str = "copy"  # copy | skip-identical | collision-renamed
    project: Optional[str] = None

    def as_record(self) -> Dict:
        data = asdict(self)
        return data


def _disambiguate(dest: str, taken: set) -> str:
    """Return a dest path not already in ``taken`` by inserting ' (n)'."""
    if dest not in taken:
        return dest
    root, ext = os.path.splitext(dest)
    counter = 2
    while f"{root} ({counter}){ext}" in taken:
        counter += 1
    return f"{root} ({counter}){ext}"


def plan_moves(
    infos: List[MediaInfo],
    dest_root: str,
    projects: Optional[Mapping[str, Sequence[str]]] = None,
) -> List[PlannedMove]:
    """Compute where every file should go, resolving collisions deterministically.

    Without ``projects`` every file gets one destination in the plain layout.
    With ``projects`` (source path -> project names) a file gets one copy per
    project it's assigned to, and files assigned to none are left out.

    Files are ordered by source path so the plan is stable across runs. Two
    different sources that would land on the same destination get a numbered
    suffix; a source already sitting at its destination is marked skip.
    """
    dest_root = os.path.abspath(dest_root)
    ordered = sorted(infos, key=lambda i: i.path)
    lookup = None
    if projects is not None:
        lookup = {_source_key(path): names for path, names in projects.items()}
    taken: set = set()
    plan: List[PlannedMove] = []

    for info in ordered:
        if lookup is None:
            targets: List[Optional[str]] = [None]
        else:
            targets = list(_unique_projects(lookup.get(_source_key(info.path), [])))
        for project in targets:
            target = os.path.join(dest_root, dest_relpath(info, project))
            if os.path.abspath(info.path) == target:
                action = "skip-identical"
            elif target in taken:
                target = _disambiguate(target, taken)
                action = "collision-renamed"
            else:
                action = "copy"
            taken.add(target)
            plan.append(PlannedMove(
                source=os.path.abspath(info.path),
                dest=target,
                media_kind=info.media_kind,
                camera=camera_label(info),
                date=date_label(info),
                size=info.size_bytes,
                action=action,
                project=project,
            ))
    return plan


# ---------------------------------------------------------------------------
# Verification helpers
# ---------------------------------------------------------------------------

def sha256_of(path: str, chunk: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def files_match(source: str, dest: str, *, checksum: bool) -> bool:
    """Confirm dest is a faithful copy of source (size, optionally checksum)."""
    if not (os.path.exists(source) and os.path.exists(dest)):
        return False
    if os.path.getsize(source) != os.path.getsize(dest):
        return False
    if checksum:
        return sha256_of(source) == sha256_of(dest)
    return True


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

class OrganizeManifest:
    """A record of copy operations, so originals can be removed safely later."""

    def __init__(self, path: str, dest_root: str) -> None:
        self.path = path
        self.dest_root = os.path.abspath(dest_root)
        # Keyed by (source, dest) so repeated runs update rather than duplicate.
        self.operations: Dict[str, Dict] = {}

    @staticmethod
    def key(source: str, dest: str) -> str:
        return f"{os.path.abspath(source)}\x00{os.path.abspath(dest)}"

    @classmethod
    def load(cls, path: str, dest_root: str) -> "OrganizeManifest":
        manifest = cls(path, dest_root)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    data = json.load(handle)
                if isinstance(data, dict):
                    for op in data.get("operations", []) or []:
                        if isinstance(op, dict) and op.get("source") and op.get("dest"):
                            manifest.operations[
                                cls.key(op["source"], op["dest"])
                            ] = op
            except (json.JSONDecodeError, OSError, ValueError):
                pass  # Treat an unreadable manifest as empty.
        return manifest

    def record(self, op: Dict) -> None:
        self.operations[self.key(op["source"], op["dest"])] = op

    def pending_deletions(self) -> List[Dict]:
        return [
            op for op in self.operations.values()
            if op.get("status") == "copied" and not op.get("source_deleted")
        ]

    def save(self, generated_at: datetime) -> None:
        payload = {
            "schema": MANIFEST_SCHEMA,
            "generated_by": "MetaScraper",
            "updated_at": generated_at.isoformat(),
            "dest_root": self.dest_root,
            "operations": list(self.operations.values()),
        }
        os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

@dataclass
class OrganizeResult:
    copied: int = 0
    skipped: int = 0
    renamed: int = 0
    failed: int = 0
    failures: List[str] = field(default_factory=list)
    plan: List[PlannedMove] = field(default_factory=list)
    cancelled: bool = False


def _resolve_ondisk_collision(dest: str, source: str, *, checksum: bool) -> str:
    """Return a destination path that won't clobber a different existing file.

    If ``dest`` is free, or already holds an identical copy of ``source``, it is
    returned unchanged. Otherwise ' (n)' is appended until a free (or identical)
    path is found.
    """
    if not os.path.exists(dest) or files_match(source, dest, checksum=checksum):
        return dest
    root, ext = os.path.splitext(dest)
    counter = 2
    while True:
        candidate = f"{root} ({counter}){ext}"
        if not os.path.exists(candidate) or files_match(
            source, candidate, checksum=checksum
        ):
            return candidate
        counter += 1


def _safe_copy(source: str, dest: str) -> None:
    """Copy source to dest via a temp file so a partial copy is never left."""
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = dest + ".part"
    if os.path.exists(tmp):
        os.remove(tmp)
    shutil.copy2(source, tmp)  # copy2 preserves mtime/metadata
    os.replace(tmp, dest)      # atomic within the destination filesystem


def execute_plan(
    plan: List[PlannedMove],
    manifest: OrganizeManifest,
    *,
    checksum: bool = False,
    generated_at: Optional[datetime] = None,
    on_event: Optional[Callable[[str, PlannedMove], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> OrganizeResult:
    """Copy each planned file into place and verify it, updating the manifest.

    ``should_stop`` is checked between files; when it returns True the run ends
    early, and the manifest still records every copy made so far.
    """
    generated_at = generated_at or datetime.now().astimezone()
    result = OrganizeResult(plan=plan)

    def notify(kind: str, move: PlannedMove) -> None:
        if on_event:
            on_event(kind, move)

    try:
        _execute(plan, manifest, result, checksum, generated_at, notify, should_stop)
    finally:
        manifest.save(generated_at)
    return result


def _execute(plan, manifest, result, checksum, generated_at, notify, should_stop):
    for move in plan:
        if should_stop and should_stop():
            result.cancelled = True
            return
        if move.action == "skip-identical":
            result.skipped += 1
            notify("skip", move)
            continue

        # Never overwrite a *different* file already on disk (e.g. a collision
        # with an earlier run). Rename to a free name instead; an identical
        # file already there is left as-is and re-recorded.
        resolved = _resolve_ondisk_collision(move.dest, move.source, checksum=checksum)
        if resolved != move.dest:
            move.dest = resolved
            if move.action == "copy":
                move.action = "collision-renamed"

        # If the destination already holds a faithful copy, don't re-copy.
        if os.path.exists(move.dest) and files_match(
            move.source, move.dest, checksum=checksum
        ):
            result.skipped += 1
            manifest.record(_op_record(move, generated_at, "copied"))
            notify("already", move)
            continue

        try:
            _safe_copy(move.source, move.dest)
            if not files_match(move.source, move.dest, checksum=checksum):
                raise IOError("verification failed after copy")
        except Exception as exc:  # noqa: BLE001
            result.failed += 1
            result.failures.append(f"{move.source} → {move.dest}: {exc}")
            manifest.record(_op_record(move, generated_at, "failed", str(exc)))
            notify("fail", move)
            continue

        if move.action == "collision-renamed":
            result.renamed += 1
        result.copied += 1
        manifest.record(_op_record(move, generated_at, "copied", checksum=checksum))
        notify("copy", move)


def _op_record(
    move: PlannedMove,
    generated_at: datetime,
    status: str,
    error: Optional[str] = None,
    checksum: bool = False,
) -> Dict:
    record = {
        "source": move.source,
        "dest": move.dest,
        "media_kind": move.media_kind,
        "camera": move.camera,
        "date": move.date,
        "size": move.size,
        "action": move.action,
        "project": move.project,
        "status": status,
        "copied_at": generated_at.isoformat() if status == "copied" else None,
        "source_deleted": False,
    }
    if status == "copied" and checksum:
        try:
            record["sha256"] = sha256_of(move.dest)
        except OSError:
            record["sha256"] = None
    if error:
        record["error"] = error
    return record


# ---------------------------------------------------------------------------
# Finalize — the "move later" step: delete originals that safely re-verify
# ---------------------------------------------------------------------------

@dataclass
class FinalizeResult:
    deleted: int = 0
    skipped: int = 0
    failed: int = 0
    freed_bytes: int = 0
    problems: List[str] = field(default_factory=list)
    cancelled: bool = False


def finalize_moves(
    manifest: OrganizeManifest,
    *,
    checksum: bool = False,
    dry_run: bool = False,
    generated_at: Optional[datetime] = None,
    on_event: Optional[Callable[[str, Dict], None]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> FinalizeResult:
    """Delete originals whose copies re-verify against the manifest.

    A file copied into several projects has several copies; its original is
    deleted only when *every* one of them still exists and matches (size, and
    checksum when requested), and no copy of it failed. Anything else is left
    in place and reported. With ``dry_run`` the deletions are only counted.
    Counts are per original file, not per copy.
    """
    generated_at = generated_at or datetime.now().astimezone()
    result = FinalizeResult()

    def notify(kind: str, op: Dict) -> None:
        if on_event:
            on_event(kind, op)

    groups: Dict[str, List[Dict]] = {}
    for op in manifest.pending_deletions():
        groups.setdefault(_source_key(op["source"]), []).append(op)

    # A failed copy blocks deletion unless that same project later got a copy.
    copied_to = {(_source_key(op["source"]), op.get("project"))
                 for op in manifest.operations.values()
                 if op.get("status") == "copied"}
    failed_copies: Dict[str, List[Dict]] = {}
    for op in manifest.operations.values():
        key = _source_key(op["source"])
        if op.get("status") == "failed" and (key, op.get("project")) not in copied_to:
            failed_copies.setdefault(key, []).append(op)

    try:
        for key, ops in groups.items():
            if should_stop and should_stop():
                result.cancelled = True
                break
            _finalize_one(ops, failed_copies.get(key, []), result, checksum,
                          dry_run, generated_at, notify)
    finally:
        if not dry_run:
            manifest.save(generated_at)
    return result


def _finalize_one(ops, failed_ops, result, checksum, dry_run, generated_at, notify):
    source = ops[0]["source"]

    if not os.path.exists(source):
        # Already gone (e.g. a previous finalize); mark it done.
        for op in ops:
            op["source_deleted"] = True
            notify("gone", op)
        result.skipped += 1
        return

    if failed_ops:
        result.failed += 1
        where = ", ".join(op["dest"] for op in failed_ops)
        result.problems.append(f"{source}: a copy failed ({where}) — original kept")
        notify("unverified", ops[0])
        return

    source_digest: Dict[str, str] = {}

    def verified(op: Dict) -> bool:
        dest = op["dest"]
        if not (os.path.exists(dest)
                and os.path.getsize(source) == os.path.getsize(dest)):
            return False
        if checksum or op.get("sha256"):
            if "sha" not in source_digest:
                source_digest["sha"] = sha256_of(source)
            return source_digest["sha"] == sha256_of(dest)
        return True

    bad = [op for op in ops if not verified(op)]
    if bad:
        result.failed += 1
        for op in bad:
            result.problems.append(
                f"{source}: copy at {op['dest']} did not verify — original kept"
            )
        notify("unverified", bad[0])
        return

    size = max((op.get("size") or 0) for op in ops)
    if dry_run:
        result.deleted += 1
        result.freed_bytes += size
        notify("would-delete", ops[0])
        return

    try:
        os.remove(source)
    except OSError as exc:
        result.failed += 1
        result.problems.append(f"{source}: could not delete — {exc}")
        notify("fail", ops[0])
        return

    for op in ops:
        op["source_deleted"] = True
        op["source_deleted_at"] = generated_at.isoformat()
    result.deleted += 1
    result.freed_bytes += size
    notify("delete", ops[0])
