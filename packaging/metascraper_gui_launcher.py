"""Entry point PyInstaller bundles into MetaScraper.exe.

``MetaScraper.exe --cli <command> ...`` runs the command-line tool instead of
the window (e.g. ``--cli catalog D:\\Footage -o D:\\Catalog``), so the packaged
app can be scripted — and smoke-tested in CI — without a Python install. The
exe is windowed, so it prints nothing; check the exit code and output files.
"""

import os
import sys
import tempfile
import traceback

CLI_ERROR_LOG = os.path.join(tempfile.gettempdir(), "MetaScraper-cli-error.log")


def run() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        from metascraper import cli
        try:
            return cli.run(sys.argv[2:])
        except Exception:  # noqa: BLE001
            # A windowed exe has no console, and an unhandled error would pop
            # up a dialog that blocks scripts; log it and fail instead.
            if sys.stderr is not None:
                traceback.print_exc()
            with open(CLI_ERROR_LOG, "w", encoding="utf-8") as handle:
                traceback.print_exc(file=handle)
            return 3
    from metascraper.gui import main
    return main()


if __name__ == "__main__":
    raise SystemExit(run())
