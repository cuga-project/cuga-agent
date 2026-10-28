"""Verify license/notice failures before image smoke checks load models."""

from types import SimpleNamespace
from pathlib import Path

import pytest

from scripts import verify_airgap, verify_airgap_slim, verify_image_licenses

pytestmark = pytest.mark.unit


@pytest.fixture
def image_licenses(tmp_path, monkeypatch):
    texts = {"licenses/LICENSE": "Apache License\n", "licenses/NOTICE": "CUGA notices\n"}
    for path, text in texts.items():
        (tmp_path / path.rsplit("/", 1)[-1]).write_text(text)
    monkeypatch.setattr(
        verify_image_licenses, "distribution", lambda name: SimpleNamespace(read_text=texts.get)
    )
    return tmp_path, texts


def test_matching_image_and_package_notices_pass(image_licenses):
    root, _ = image_licenses
    verify_image_licenses.verify_image_licenses(root)


@pytest.mark.parametrize("name", ["LICENSE", "NOTICE"])
@pytest.mark.parametrize("content", [None, "", " \n"])
def test_image_notices_must_exist_and_be_nonempty(image_licenses, name, content):
    root, _ = image_licenses
    path = root / name
    if content is None:
        path.unlink()
    else:
        path.write_text(content)
    with pytest.raises(RuntimeError, match="Required image license file is missing or empty"):
        verify_image_licenses.verify_image_licenses(root)


@pytest.mark.parametrize("name", ["LICENSE", "NOTICE"])
@pytest.mark.parametrize("content", [None, "", "outdated notice\n"])
def test_installed_package_must_preserve_image_notices(image_licenses, name, content):
    root, texts = image_licenses
    texts[f"licenses/{name}"] = content
    with pytest.raises(RuntimeError, match="Installed CUGA license file is missing or differs"):
        verify_image_licenses.verify_image_licenses(root)


@pytest.mark.parametrize("verifier", [verify_airgap, verify_airgap_slim])
def test_image_smoke_checks_validate_notices_before_loading_models(verifier, monkeypatch):
    monkeypatch.setattr(verify_airgap, "find_spec", lambda name: None)
    monkeypatch.setattr(verify_airgap, "distributions", lambda: [])

    def reject_missing_notices(root):
        # The helper may live in site-packages; resolve /app from the source
        # entry point, which Docker invokes by absolute path.
        assert root == Path(verifier.__file__).resolve().parents[2]
        raise RuntimeError("missing notices")

    monkeypatch.setattr(verify_image_licenses, "verify_image_licenses", reject_missing_notices)
    with pytest.raises(RuntimeError, match="missing notices"):
        verifier.main()
