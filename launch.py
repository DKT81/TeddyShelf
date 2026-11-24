"""PyInstaller entry point and isolated source-worker launcher."""

import sys
from pathlib import Path

# An editable installation may point to a different checkout. Source workers
# must resolve this checkout first; frozen builds use their bundled modules.
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker-job":
        from teddystudio.queue import run_child

        raise SystemExit(run_child(sys.argv[2]))
    from teddystudio.app import main

    raise SystemExit(main())
