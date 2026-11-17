"""PyInstaller entry point, also runnable from an editable installation."""

if __name__ == "__main__":
    import sys

    if len(sys.argv) == 3 and sys.argv[1] == "--worker-job":
        from teddystudio.queue import run_child

        raise SystemExit(run_child(sys.argv[2]))
    from teddystudio.app import main

    raise SystemExit(main())
