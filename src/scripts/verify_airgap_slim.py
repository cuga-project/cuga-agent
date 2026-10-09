"""Smoke-test bundled assets for the slim image. Run with Docker's --network=none.

Slim variant of verify_airgap.py: omits the Evolve and Docling checks, since
neither is installed (torch-free image — see Dockerfile.ubi.slim).
"""


def main() -> None:
    from scripts.preload_models import preload_fastembed, preload_tiktoken

    preload_fastembed()
    preload_tiktoken()

    print("Airgap asset verification passed (slim: fastembed + tiktoken only)")


if __name__ == "__main__":
    main()
