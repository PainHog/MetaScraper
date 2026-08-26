"""Unit tests for the organizer's path planning and safe copy/finalize."""

import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metascraper import organizer
from metascraper.models import MediaInfo


def _info(path, kind="Video", make=None, model=None, recorded=None,
          size=100, modified=None):
    return MediaInfo(
        path=os.path.abspath(path),
        name=os.path.basename(path),
        ext=os.path.splitext(path)[1],
        media_kind=kind,
        size_bytes=size,
        camera_make=make,
        camera_model=model,
        recorded_at=recorded,
        fs_modified=modified,
    )


REC = datetime(2024, 5, 11, 18, 32, 7, tzinfo=timezone.utc)


def test_sanitize_component():
    assert organizer.sanitize_component("Sony/A7:M4") == "Sony_A7_M4"
    assert organizer.sanitize_component("trailing.  ") == "trailing"
    assert organizer.sanitize_component("") == "Untitled"
    assert organizer.sanitize_component("CON").lower().startswith("con_")


def test_camera_label_combines_make_and_model():
    assert organizer.camera_label(_info("a.mp4", make="Sony", model="ILCE-7M4")) \
        == "Sony ILCE-7M4"
    # Make already embedded in model shouldn't duplicate.
    assert organizer.camera_label(_info("a.mp4", make="Apple", model="Apple iPhone")) \
        == "Apple iPhone"
    assert organizer.camera_label(_info("a.mp4")) == organizer.UNKNOWN_CAMERA


def test_date_label_prefers_recorded_then_modified():
    assert organizer.date_label(_info("a.mp4", recorded=REC)) == "2024-05-11"
    mod = datetime(2022, 1, 2, tzinfo=timezone.utc)
    assert organizer.date_label(_info("a.mp4", modified=mod)) == "2022-01-02"
    assert organizer.date_label(_info("a.mp4")) == organizer.UNKNOWN_DATE


def test_dest_relpath_camera_then_date_under_kind():
    info = _info("/src/IMG.MOV", kind="Video", make="Sony", model="ILCE-7M4",
                 recorded=REC)
    rel = organizer.dest_relpath(info)
    assert rel == os.path.join("Video", "Sony ILCE-7M4", "2024-05-11", "IMG.MOV")


def test_plan_moves_disambiguates_collisions():
    # Two different source files that map to the same camera/date/name.
    a = _info("/src/a/CLIP.MOV", make="Sony", model="X", recorded=REC)
    b = _info("/src/b/CLIP.MOV", make="Sony", model="X", recorded=REC)
    plan = organizer.plan_moves([a, b], "/dest")
    dests = [p.dest for p in plan]
    assert len(set(dests)) == 2  # no two files share a destination
    assert any(p.action == "collision-renamed" for p in plan)


def test_plan_moves_is_order_independent():
    a = _info("/src/a/CLIP.MOV", make="Sony", model="X", recorded=REC)
    b = _info("/src/b/CLIP.MOV", make="Sony", model="X", recorded=REC)
    plan1 = {p.source: p.dest for p in organizer.plan_moves([a, b], "/dest")}
    plan2 = {p.source: p.dest for p in organizer.plan_moves([b, a], "/dest")}
    assert plan1 == plan2


def _write(path, data=b"hello world"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def test_execute_plan_copies_and_leaves_originals(tmp_path):
    src = tmp_path / "src" / "take.wav"
    _write(str(src), b"\x00" * 500)
    info = _info(str(src), kind="Audio", make="Zoom", model="H6", recorded=REC)
    dest_root = str(tmp_path / "lib")
    plan = organizer.plan_moves([info], dest_root)

    manifest = organizer.OrganizeManifest(
        os.path.join(dest_root, organizer.MANIFEST_NAME), dest_root
    )
    result = organizer.execute_plan(plan, manifest, checksum=True)

    assert result.copied == 1 and result.failed == 0
    expected = os.path.join(dest_root, "Audio", "Zoom H6", "2024-05-11", "take.wav")
    assert os.path.exists(expected)
    assert os.path.exists(str(src))  # original untouched
    assert os.path.exists(manifest.path)  # manifest written
    # No stray temp files left behind.
    assert not os.path.exists(expected + ".part")


def test_execute_plan_is_idempotent(tmp_path):
    src = tmp_path / "src" / "take.wav"
    _write(str(src), b"\x00" * 500)
    info = _info(str(src), kind="Audio", make="Zoom", model="H6", recorded=REC)
    dest_root = str(tmp_path / "lib")
    manifest_path = os.path.join(dest_root, organizer.MANIFEST_NAME)

    plan = organizer.plan_moves([info], dest_root)
    organizer.execute_plan(plan, organizer.OrganizeManifest(manifest_path, dest_root))
    # Second run: destination already holds a faithful copy -> skipped.
    result2 = organizer.execute_plan(
        plan, organizer.OrganizeManifest.load(manifest_path, dest_root)
    )
    assert result2.copied == 0
    assert result2.skipped == 1


def test_finalize_deletes_only_verified_originals(tmp_path):
    src = tmp_path / "src" / "take.wav"
    _write(str(src), b"\x00" * 500)
    info = _info(str(src), kind="Audio", make="Zoom", model="H6", recorded=REC)
    dest_root = str(tmp_path / "lib")
    manifest_path = os.path.join(dest_root, organizer.MANIFEST_NAME)

    plan = organizer.plan_moves([info], dest_root)
    manifest = organizer.OrganizeManifest(manifest_path, dest_root)
    organizer.execute_plan(plan, manifest, checksum=True)

    # Preview must not delete.
    manifest2 = organizer.OrganizeManifest.load(manifest_path, dest_root)
    preview = organizer.finalize_moves(manifest2, checksum=True, dry_run=True)
    assert preview.deleted == 1
    assert os.path.exists(str(src))  # still there after a dry run

    # Real finalize deletes the verified original.
    manifest3 = organizer.OrganizeManifest.load(manifest_path, dest_root)
    result = organizer.finalize_moves(manifest3, checksum=True)
    assert result.deleted == 1
    assert not os.path.exists(str(src))
    # Manifest now records the deletion; a second finalize is a no-op.
    again = organizer.finalize_moves(
        organizer.OrganizeManifest.load(manifest_path, dest_root), checksum=True
    )
    assert again.deleted == 0


def test_cross_run_collision_never_overwrites(tmp_path):
    # Two DIFFERENT files that map to the same camera/date/name, organized in
    # separate runs, must both survive in the library — no silent overwrite.
    dest_root = str(tmp_path / "lib")
    manifest_path = os.path.join(dest_root, organizer.MANIFEST_NAME)

    a = tmp_path / "a" / "CLIP.MOV"
    _write(str(a), b"AAAA" * 100)
    info_a = _info(str(a), make="Sony", model="X", recorded=REC, size=400)
    organizer.execute_plan(
        organizer.plan_moves([info_a], dest_root),
        organizer.OrganizeManifest(manifest_path, dest_root), checksum=True,
    )

    b = tmp_path / "b" / "CLIP.MOV"
    _write(str(b), b"BBBBBB" * 100)  # different content (and size)
    info_b = _info(str(b), make="Sony", model="X", recorded=REC, size=600)
    result = organizer.execute_plan(
        organizer.plan_moves([info_b], dest_root),
        organizer.OrganizeManifest.load(manifest_path, dest_root), checksum=True,
    )
    assert result.copied == 1

    base = os.path.join(dest_root, "Video", "Sony X", "2024-05-11")
    copies = sorted(os.listdir(base))
    assert copies == ["CLIP (2).MOV", "CLIP.MOV"]
    # The first copy is intact (still A's content).
    with open(os.path.join(base, "CLIP.MOV"), "rb") as handle:
        assert handle.read() == b"AAAA" * 100


def test_finalize_keeps_original_if_copy_missing(tmp_path):
    src = tmp_path / "src" / "take.wav"
    _write(str(src), b"\x00" * 500)
    info = _info(str(src), kind="Audio", make="Zoom", model="H6", recorded=REC)
    dest_root = str(tmp_path / "lib")
    manifest_path = os.path.join(dest_root, organizer.MANIFEST_NAME)
    manifest = organizer.OrganizeManifest(manifest_path, dest_root)
    organizer.execute_plan(organizer.plan_moves([info], dest_root), manifest)

    # Simulate the copy being lost/corrupted before finalize.
    copy_path = os.path.join(dest_root, "Audio", "Zoom H6", "2024-05-11", "take.wav")
    os.remove(copy_path)

    result = organizer.finalize_moves(
        organizer.OrganizeManifest.load(manifest_path, dest_root), checksum=True
    )
    assert result.deleted == 0
    assert result.failed == 1
    assert os.path.exists(str(src))  # original preserved because copy didn't verify
