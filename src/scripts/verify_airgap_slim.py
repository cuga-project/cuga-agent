"""Smoke-test bundled assets for the slim image. Run with Docker's --network=none.

Slim variant of verify_airgap.py: omits the Evolve and Docling checks, since
neither is installed (torch-free image — see Dockerfile.ubi.slim).
"""

from pathlib import Path


def main() -> None:
    from scripts.verify_image_licenses import verify_image_licenses

    verify_image_licenses(Path(__file__).resolve().parents[2])

    from scripts.preload_models import preload_fastembed, preload_tiktoken

    preload_fastembed()
    preload_tiktoken()

    print("Airgap asset verification passed (slim: fastembed + tiktoken only)")


if __name__ == "__main__":
    main()
