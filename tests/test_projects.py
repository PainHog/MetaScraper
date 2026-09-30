"""Tests for organizing into projects: planning, copying, finalize safety,
project discovery, the service layer, and the CLI --project option."""

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metascraper import cli, organizer, probe, service
from metascraper.models import MediaInfo

REC = datetime(2024, 5, 11, 18, 32, 7, tzinfo=timezone.utc)


def _write(path, data=b"\x00" * 500):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(data)


def _info(path, kind="Video", size=500):
    return MediaInfo(
        path=os.path.abspath(path), name=os.path.basename(path),
        ext=os.path.splitext(path)[1], media_kind=kind, size_bytes=size,
        camera_make="Sony", camera_model="FX3", recorded_at=REC,
    )


def _manifest(dest_root):
    return organizer.OrganizeManifest.load(
        os.path.join(dest_root, organizer.MANIFEST_NAME), dest_root)


def _organize(infos, dest_root, projects, checksum=True):
    plan = organizer.plan_moves(infos, dest_root, projects=projects)
    return organizer.execute_plan(plan, _manifest(dest_root), checksum=checksum)


# -- names + paths ----------------------------------------------------------

def test_project_name_problem():
    assert organizer.project_name_problem("Wildlife Doc 2024") is None
    assert organizer.project_name_problem("") is not None
    assert organizer.project_name_problem("   ") is not None
    assert organizer.project_name_problem("a/b") is not None
    assert organizer.project_name_problem("trailing.") is not None
    assert organizer.project_name_problem("CON") is not None
    # Reserved for the plain layout's own top-level folders.
    assert organizer.project_name_problem("video") is not None


def test_dest_relpath_with_project():
    info = _info("/src/C0001.MP4")
    assert organizer.dest_relpath(info, "Client Reel") == os.path.join(
        "Client Reel", "Video", "Sony FX3", "2024-05-11", "C0001.MP4")
    assert organizer.dest_relpath(info) == os.path.join(
        "Video", "Sony FX3", "2024-05-11", "C0001.MP4")


# -- planning -----------------------------------------------------------------

def test_plan_one_copy_per_project_and_skips_unassigned(tmp_path):
    a = _info(str(tmp_path / "a.mp4"))
    b = _info(str(tmp_path / "b.mp4"))
    c = _info(str(tmp_path / "c.mp4"))
    plan = organizer.plan_moves([a, b, c], "/lib", projects={
        a.path: ["Doc", "Reel"],
        b.path: ["Reel"],
        # c isn't assigned to anything
    })
    got = sorted((os.path.basename(m.source), m.project) for m in plan)
    assert got == [("a.mp4", "Doc"), ("a.mp4", "Reel"), ("b.mp4", "Reel")]
    for move in plan:
        assert move.dest.startswith(os.path.join(os.path.abspath("/lib"), move.project))


def test_plan_dedupes_projects_that_share_a_folder(tmp_path):
    a = _info(str(tmp_path / "a.mp4"))
    plan = organizer.plan_moves([a], "/lib", projects={a.path: ["Doc", "doc", " "]})
    assert [m.project for m in plan] == ["Doc"]


def test_plan_without_projects_keeps_plain_layout(tmp_path):
    a = _info(str(tmp_path / "a.mp4"))
    plan = organizer.plan_moves([a], "/lib")
    assert len(plan) == 1 and plan[0].project is None
    assert os.sep + "Video" + os.sep in plan[0].dest


# -- copying + finalize -------------------------------------------------------

def test_copies_into_every_project_and_records_project(tmp_path):
    src = str(tmp_path / "src" / "clip.mp4")
    _write(src)
    lib = str(tmp_path / "lib")
    result = _organize([_info(src)], lib, {src: ["Doc", "Reel"]})
    assert result.copied == 2 and result.failed == 0
    for project in ("Doc", "Reel"):
        assert os.path.exists(os.path.join(
            lib, project, "Video", "Sony FX3", "2024-05-11", "clip.mp4"))
    with open(os.path.join(lib, organizer.MANIFEST_NAME), encoding="utf-8") as fh:
        ops = json.load(fh)["operations"]
    assert sorted(op["project"] for op in ops) == ["Doc", "Reel"]
    assert os.path.exists(src)  # original untouched


def test_finalize_deletes_original_once_all_copies_verify(tmp_path):
    src = str(tmp_path / "src" / "clip.mp4")
    _write(src)
    lib = str(tmp_path / "lib")
    _organize([_info(src)], lib, {src: ["Doc", "Reel"]})

    preview = organizer.finalize_moves(_manifest(lib), checksum=True, dry_run=True)
    assert preview.deleted == 1          # counted per original, not per copy
    assert preview.freed_bytes == 500
    assert os.path.exists(src)

    result = organizer.finalize_moves(_manifest(lib), checksum=True)
    assert result.deleted == 1 and result.failed == 0
    assert not os.path.exists(src)
    assert all(op["source_deleted"] for op in _manifest(lib).operations.values())


def test_finalize_keeps_original_if_any_project_copy_is_missing(tmp_path):
    src = str(tmp_path / "src" / "clip.mp4")
    _write(src)
    lib = str(tmp_path / "lib")
    _organize([_info(src)], lib, {src: ["Doc", "Reel"]})
    reel_copy = os.path.join(lib, "Reel", "Video", "Sony FX3", "2024-05-11", "clip.mp4")
    os.remove(reel_copy)

    result = organizer.finalize_moves(_manifest(lib), checksum=True)
    assert result.deleted == 0 and result.failed == 1
    assert os.path.exists(src)
    assert any(reel_copy in problem for problem in result.problems)
    # Neither copy is marked as having had its original deleted.
    assert not any(op["source_deleted"] for op in _manifest(lib).operations.values())


def test_finalize_keeps_original_if_a_copy_is_corrupt(tmp_path):
    src = str(tmp_path / "src" / "clip.mp4")
    _write(src, b"A" * 500)
    lib = str(tmp_path / "lib")
    _organize([_info(src)], lib, {src: ["Doc", "Reel"]})
    doc_copy = os.path.join(lib, "Doc", "Video", "Sony FX3", "2024-05-11", "clip.mp4")
    _write(doc_copy, b"B" * 500)  # same size, different content

    result = organizer.finalize_moves(_manifest(lib), checksum=True)
    assert result.deleted == 0
    assert os.path.exists(src)


def test_failed_copy_blocks_finalize_until_that_project_succeeds(tmp_path):
    src = str(tmp_path / "src" / "clip.mp4")
    _write(src)
    lib = str(tmp_path / "lib")
    _organize([_info(src)], lib, {src: ["Doc"]})
    manifest = _manifest(lib)
    manifest.record({
        "source": os.path.abspath(src), "dest": os.path.join(lib, "Reel", "x.mp4"),
        "project": "Reel", "status": "failed", "source_deleted": False,
    })
    manifest.save(REC)

    result = organizer.finalize_moves(_manifest(lib), checksum=True)
    assert result.deleted == 0 and result.failed == 1
    assert os.path.exists(src)

    # Once Reel gets a good copy, the original can go.
    _organize([_info(src)], lib, {src: ["Reel"]})
    result = organizer.finalize_moves(_manifest(lib), checksum=True)
    assert result.deleted == 1
    assert not os.path.exists(src)


def test_execute_plan_saves_manifest_when_stopped_early(tmp_path):
    lib = str(tmp_path / "lib")
    infos = []
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        path = str(tmp_path / "src" / name)
        _write(path)
        infos.append(_info(path))
    plan = organizer.plan_moves(infos, lib, projects={i.path: ["Doc"] for i in infos})
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] > 1   # let exactly one file through

    result = organizer.execute_plan(plan, _manifest(lib), should_stop=stop)
    assert result.cancelled and result.copied == 1
    assert len(_manifest(lib).pending_deletions()) == 1


# -- discovery of existing projects -------------------------------------------

def test_list_and_copied_projects(tmp_path):
    src = str(tmp_path / "src" / "clip.mp4")
    _write(src)
    lib = str(tmp_path / "lib")
    _organize([_info(src)], lib, {src: ["Doc", "Reel"]})
    # A project folder made by hand (has a Video tree) is picked up...
    os.makedirs(os.path.join(lib, "Older Project", "Audio"))
    # ...but unrelated folders are not.
    os.makedirs(os.path.join(lib, "Random Stuff"))
    os.makedirs(os.path.join(lib, "Video"))

    assert organizer.list_projects(lib) == ["Doc", "Reel", "Older Project"]
    copied = organizer.copied_projects(lib)
    assert copied[os.path.normcase(os.path.abspath(src))] == ["Doc", "Reel"]
    assert service.list_projects(str(tmp_path / "missing")) == []


# -- service ------------------------------------------------------------------

def test_service_organize_with_scanned_infos(tmp_path, monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda *_a, **_k: None)
    src = tmp_path / "in"
    _write(str(src / "one.mp4"))
    _write(str(src / "two.wav"))
    _write(str(src / "three.mp4"))
    lib = tmp_path / "lib"
    tools = service.resolve_tools()

    scan = service.scan_media(service.ScanOptions(folders=[str(src)]), tools)
    assert len(scan.infos) == 3
    by_name = {os.path.basename(i.path): i.path for i in scan.infos}

    opts = service.OrganizeOptions(
        folders=[str(src)], dest=str(lib),
        projects={by_name["one.mp4"]: ["Doc", "Reel"], by_name["two.wav"]: ["Doc"]},
    )
    outcome = service.run_organize(opts, tools, infos=scan.infos)
    assert outcome.unassigned == 1
    assert outcome.result.copied == 3
    assert (lib / "Doc").is_dir() and (lib / "Reel").is_dir()
    assert not any(p.name == "three.mp4" for p in lib.rglob("*"))


def test_service_organize_ignores_files_already_in_library(tmp_path, monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda *_a, **_k: None)
    lib = tmp_path / "lib"
    inside = str(lib / "Doc" / "Video" / "x.mp4")
    _write(inside)
    opts = service.OrganizeOptions(folders=[str(lib)], dest=str(lib),
                                   projects={inside: ["Reel"]})
    outcome = service.run_organize(opts, service.resolve_tools(),
                                   infos=[_info(inside)])
    assert outcome.no_media


def test_catalog_stop_saves_what_was_read(tmp_path, monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda *_a, **_k: None)
    src = tmp_path / "in"
    for name in ("a.wav", "b.wav", "c.wav"):
        _write(str(src / name))
    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] > 2

    result = service.run_catalog(
        service.CatalogOptions(folders=[str(src)], output_dir=str(tmp_path / "out")),
        service.resolve_tools(), should_stop=stop)
    assert result.cancelled and result.processed == 2
    assert result.summary["count"] == 2
    assert (tmp_path / "out" / "Master_Catalog.xlsx").exists()


# -- CLI ------------------------------------------------------------------------

def test_cli_organize_into_projects(tmp_path, monkeypatch):
    monkeypatch.setattr(probe.shutil, "which", lambda *_a, **_k: None)
    src = tmp_path / "in"
    _write(str(src / "clip.mp4"))
    lib = tmp_path / "lib"
    code = cli.run(["organize", str(src), "-o", str(lib), "-q",
                    "--project", "Doc", "-p", "Reel"])
    assert code == 0
    assert list((lib / "Doc").rglob("clip.mp4"))
    assert list((lib / "Reel").rglob("clip.mp4"))
    assert not (lib / "Video").exists()

    code = cli.run(["finalize", str(lib), "--yes", "--checksum"])
    assert code == 0
    assert not (src / "clip.mp4").exists()
    assert list((lib / "Doc").rglob("clip.mp4")) and list((lib / "Reel").rglob("clip.mp4"))


def test_cli_rejects_bad_project_name(tmp_path, capsys):
    code = cli.run(["organize", str(tmp_path), "-o", str(tmp_path / "lib"),
                    "--project", "a/b"])
    assert code == 1
    assert "Invalid project name" in capsys.readouterr().err
