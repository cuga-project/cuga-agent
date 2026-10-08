def run_cli():
    """Bootstrap local manager storage before settings and provider imports."""
    import sys
    import os

    args = sys.argv[1:]
    container_preset = bool(os.environ.get("CUGA_DEMO_MODE"))
    if not container_preset and any(args[i : i + 2] == ["start", "manager"] for i in range(len(args))):
        from cuga.local_setup import prepare_local_manager

        prepare_local_manager()
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
