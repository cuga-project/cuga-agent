#!/usr/bin/env python3
"""Download the Activepieces piece tarballs the PoC needs, at build time.

Activepieces resolves its catalog from cloud.activepieces.com and pulls each
package from npm. Neither host is reachable from a restricted cluster, so the
archives are baked into the image here and uploaded through the local API on
first boot instead.

Versions come from the PINNED table in scripts/ap_pieces.py so the image and
the local development flow cannot drift apart.
"""

from __future__ import annotations

import ast
import json
import sys
import urllib.request
from pathlib import Path

REGISTRY = "https://registry.npmjs.org"


def pinned_versions(source: Path) -> dict[str, str]:
    for node in ast.parse(source.read_text()).body:
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", None) == "PINNED" for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise SystemExit(f"no PINNED table found in {source}")


def main() -> None:
    source, destination = Path(sys.argv[1]), Path(sys.argv[2])
    destination.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, dict[str, str]] = {}
    for name, version in sorted(pinned_versions(source).items()):
        # Scoped packages publish their tarball under the unscoped basename.
        basename = name.split("/")[-1]
        archive = f"{basename}-{version}.tgz"
        url = f"{REGISTRY}/{name}/-/{archive}"
        urllib.request.urlretrieve(url, destination / archive)
        size = (destination / archive).stat().st_size
        if size == 0:
            raise SystemExit(f"{url} produced an empty archive")
        manifest[name] = {"version": version, "archive": archive}
        print(f"fetched {archive} ({size} bytes)")

    if not manifest:
        raise SystemExit("PINNED table is empty; nothing to bake")
    (destination / "pieces.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    print(f"wrote manifest for {len(manifest)} pieces")


if __name__ == "__main__":
    main()
