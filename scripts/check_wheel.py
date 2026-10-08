"""Fail a release build if the wheel cannot serve the complete manager frontend."""

import re
import sys
from pathlib import Path
from zipfile import ZipFile


def check_wheel(path: Path) -> None:
    with ZipFile(path) as wheel:
        names = set(wheel.namelist())
        root = "cuga/frontend/dist/"
        html = wheel.read(root + "index.html").decode()
        assets = re.findall(r'(?:src|href)=["\']([^"\']+\.(?:js|css))["\']', html)
        if not assets:
            raise ValueError("Wheel frontend has no JavaScript or CSS assets")
        for asset in assets:
            if asset.startswith(("https://", "http://")):
                continue
            if root + asset.lstrip("/") not in names:
                raise ValueError(f"Wheel frontend asset is missing: {asset}")
        for required in ("cuga/local_setup.py", "cuga/setup_cli.py", "cuga/setup_terminal.py"):
            if required not in names:
                raise ValueError(f"Wheel setup module is missing: {required}")
        bundles = b"".join(
            wheel.read(name) for name in names if name.startswith(root) and name.endswith(".js")
        )
        if b"Set up your first agent" in bundles:
            raise ValueError("Rebuild the frontend: removed browser setup is still in the wheel")
    print(f"Verified {path.name}: terminal setup and all frontend assets are packaged.")


if __name__ == "__main__":
    check_wheel(Path(sys.argv[1]))
