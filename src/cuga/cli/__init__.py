def run_cli():
    """Prepare local configuration before settings and provider imports."""
    import sys
    import os

    args = sys.argv[1:]
    commands = [arg for arg in args if arg not in ("-v", "--verbose")]
    if commands and commands[0] == "setup":
        from cuga.setup_cli import main

        raise SystemExit(main(commands[1:]))
    container_preset = bool(os.environ.get("CUGA_DEMO_MODE"))
    if (
        not container_preset
        and commands[:2] == ["start", "manager"]
        and not any(flag in args for flag in ("--help", "-h"))
    ):
        from cuga.local_setup import prepare_local_manager
        from cuga.setup_cli import ensure_provider

        root = prepare_local_manager()
        if not ensure_provider(root):
            raise SystemExit(1)
    from cuga.cli.main import app

    app()


def __getattr__(name):
    if name == "AppManager":
        from cuga.cli.app_manager import AppManager

        return AppManager
    if name in ("app", "start_extension_browser_if_configured"):
        from importlib import import_module

        return getattr(import_module("cuga.cli.main"), name)
    raise AttributeError(name)


__all__ = ["AppManager", "app", "run_cli", "start_extension_browser_if_configured"]
