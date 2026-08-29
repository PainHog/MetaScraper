#!/usr/bin/env python3
"""A cross-platform functional smoke test of the full MetaScraper workflow.

Runs catalog -> organize -> finalize against real temporary files using the
installed `metascraper` CLI, and asserts the key outcomes. Exits non-zero on any
failure. CI runs this on Windows so every build proves the app performs on the
real target OS (it does not require FFmpeg — it exercises discovery, document
and spreadsheet generation, the copy-first organize, and the verified delete).

    python packaging/functional_smoke.py
"""

import os
import subprocess
import sys
import tempfile
import wave


def _run(args, cwd):
    print("$ metascraper", " ".join(args), flush=True)
    proc = subprocess.run([sys.executable, "-m", "metascraper", *args],
                          cwd=cwd, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    sys.stdout.write(proc.stderr)
    return proc


def _count_media(root):
    exts = (".mp4", ".mov", ".wav")
    return sum(1 for _b, _d, fs in os.walk(root)
               for f in fs if os.path.splitext(f)[1].lower() in exts)


def main() -> int:
    failures = []

    def check(label, cond):
        print(f"  [{'PASS' if cond else 'FAIL'}] {label}", flush=True)
        if not cond:
            failures.append(label)

    with tempfile.TemporaryDirectory() as work:
        src = os.path.join(work, "cards")
        os.makedirs(os.path.join(src, "DCIM"))
        os.makedirs(os.path.join(src, "AUDIO"))
        with open(os.path.join(src, "DCIM", "CLIP01.mp4"), "wb") as fh:
            fh.write(os.urandom(4096))
        with open(os.path.join(src, "DCIM", "CLIP02.mov"), "wb") as fh:
            fh.write(os.urandom(4096))
        with wave.open(os.path.join(src, "AUDIO", "TAKE01.wav"), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(48000)
            w.writeframes(b"\x00\x00" * 48000)
        total = 3

        # catalog
        cat = os.path.join(work, "Catalog")
        _run(["catalog", src, "-o", cat, "-q"], work)
        check("per-file docs created",
              os.path.isdir(os.path.join(cat, "Documents"))
              and len(os.listdir(os.path.join(cat, "Documents"))) == total)
        for name in ("Master_Catalog.docx", "Master_Catalog.xlsx",
                     "Master_Catalog.json"):
            check(f"master {name} created", os.path.exists(os.path.join(cat, name)))

        # organize (copy-first, checksum-verified)
        lib = os.path.join(work, "Library")
        _run(["organize", src, "-o", lib, "--checksum", "-q"], work)
        check("Video/ and Audio/ trees created",
              os.path.isdir(os.path.join(lib, "Video"))
              and os.path.isdir(os.path.join(lib, "Audio")))
        check("all files copied into the library", _count_media(lib) == total)
        check("originals untouched by organize", _count_media(src) == total)

        # finalize preview then real delete
        _run(["finalize", lib], work)
        check("preview leaves originals in place", _count_media(src) == total)
        _run(["finalize", lib, "--yes", "--checksum"], work)
        check("finalize removed originals", _count_media(src) == 0)
        check("organized copies remain", _count_media(lib) == total)

    print()
    if failures:
        print(f"SMOKE FAILED: {len(failures)} check(s) failed: {failures}")
        return 1
    print("SMOKE PASSED: full catalog -> organize -> finalize workflow verified.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
