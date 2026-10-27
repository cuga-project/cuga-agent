"""Verify CUGA's notices in the image and its installed wheel."""

from importlib.metadata import distribution
from pathlib import Path


def verify_image_licenses(root: Path) -> None:
    package = distribution("cuga")
    for name in ("LICENSE", "NOTICE"):
        path = root / name
        if not path.is_file() or not path.read_text().strip():
            raise RuntimeError(f"Required image license file is missing or empty: {path}")
        if package.read_text(f"licenses/{name}") != path.read_text():
            raise RuntimeError(f"Installed CUGA license file is missing or differs from the image: {name}")
