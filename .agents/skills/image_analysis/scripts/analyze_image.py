import argparse
import base64
import os
import sys
from pathlib import Path

import litellm

litellm.drop_params = True

_MEDIA_TYPE_MAP = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _is_url(source: str) -> bool:
    return source.startswith("http://") or source.startswith("https://")


def _resolve_path(source: str) -> Path:
    p = Path(source)
    if p.is_absolute() and p.exists():
        return p
    if source.startswith("/workspace/"):
        rel = Path(source[len("/workspace/"):])
        if rel.exists():
            return rel
    return p


def _download(url: str) -> Path:
    import urllib.request
    suffix = Path(url.split("?")[0]).suffix or ".jpg"
    dest = Path(f"_image_download{suffix}")
    urllib.request.urlretrieve(url, dest)
    return dest


def _build_image_content(source: str) -> dict:
    if _is_url(source):
        path = _download(source)
    else:
        path = _resolve_path(source)
    if not path.exists():
        print(f"Error: file not found: {source!r}", file=sys.stderr)
        sys.exit(1)
    data = base64.standard_b64encode(path.read_bytes()).decode("ascii")
    media_type = _MEDIA_TYPE_MAP.get(path.suffix.lower(), "image/jpeg")
    return {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{data}"}}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--question", required=True)
    args = parser.parse_args()

    model = os.environ.get("IMAGE_ANALYSIS_MODEL", "").strip()
    if not model:
        print("Error: IMAGE_ANALYSIS_MODEL is not set.", file=sys.stderr)
        sys.exit(1)

    api_key = os.environ.get("LITELLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("LITELLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL")

    if not api_key:
        print("Error: no API key found.", file=sys.stderr)
        sys.exit(1)

    completion_args: dict = {
        "model": model,
        "messages": [{"role": "user", "content": [_build_image_content(args.image), {"type": "text", "text": args.question}]}],
        "max_tokens": 1024,
        "api_key": api_key,
    }
    if base_url:
        completion_args["base_url"] = base_url.rstrip("/")
        completion_args["custom_llm_provider"] = "openai"

    try:
        response = litellm.completion(**completion_args)
        print(response.choices[0].message.content)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
