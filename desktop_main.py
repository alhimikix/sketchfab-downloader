"""Entry point shared by the one-file GUI and its optional CLI mode."""
import multiprocessing


def main():
    multiprocessing.freeze_support()
    from platform_support import configure_bundled_runtime
    configure_bundled_runtime()
    from sketchfab_local import main as run
    return run()


if __name__ == '__main__':
    raise SystemExit(main())
