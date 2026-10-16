"""Publish multi-architecture tags using only previously scanned image digests."""

import os
import re
import subprocess
from pathlib import Path


def promotion_commands(repository, ref_type, ref_name, requested, descriptors):
    if not re.fullmatch(r"ghcr\.io/[a-z0-9._/-]+", repository):
        raise ValueError("Invalid GHCR repository")
    if requested:
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,122}", requested):
            raise ValueError("Invalid image tag (maximum 123 characters, including room for -slim)")
        tags = [requested]
    elif ref_type == "tag":
        match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", ref_name)
        if not match:
            raise ValueError("Release requires a stable semantic version tag")
        major, minor, patch = match.groups()
        tags = [f"{major}.{minor}.{patch}", f"{major}.{minor}", major]
    else:
        tags = ["latest"]
    commands = []
    # Validate all four digests before changing any public release tag.
    for profile in ("full", "slim"):
        sources = [descriptors[f"{profile}-{arch}"] for arch in ("amd64", "arm64")]
        for source in sources:
            if not re.fullmatch(re.escape(repository) + r"@sha256:[0-9a-f]{64}", source):
                raise ValueError("Missing or invalid scanned image digest")
        command = ["docker", "buildx", "imagetools", "create"]
        for tag in tags:
            command += ["--tag", f"{repository}:{tag}{'-slim' if profile == 'slim' else ''}"]
        commands.append(command + sources)
    return commands


def main():
    repository = f"ghcr.io/{os.environ['REPOSITORY_OWNER'].lower()}/{os.environ['IMAGE_NAME']}"
    descriptors = {p.stem: p.read_text().strip() for p in Path("image-descriptors").glob("*.txt")}
    commands = promotion_commands(
        repository,
        os.environ["GITHUB_REF_TYPE"],
        os.environ["GITHUB_REF_NAME"],
        os.getenv("REQUESTED_TAG", ""),
        descriptors,
    )
    for command in commands:
        subprocess.run(command, check=True)


if __name__ == "__main__":
    main()
