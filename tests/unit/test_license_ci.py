"""Regression tests for fail-closed license inventory, policy and release promotion."""

import importlib.util
import json
import tarfile
import zipfile
from datetime import date
from io import BytesIO
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/licenses" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = load_script("check")
promote = load_script("promote_images")
POLICY = {"approved_expressions": ["MIT", "Apache-2.0", "Apache-2.0 WITH LLVM-exception"], "exceptions": []}
BASELINE = {"reviewed": True, "findings": []}


def report(licenses=None, name="demo", version="1"):
    pkg = {"Name": name, "Version": version}
    if licenses is not None:
        pkg["Licenses"] = licenses
    return {"SchemaVersion": 2, "Results": [{"Type": "python-pkg", "Packages": [pkg]}]}


def run_report(data, **kwargs):
    return check.evaluate(
        data,
        kwargs.pop("installed", []),
        kwargs.pop("policy", POLICY),
        kwargs.pop("baseline", BASELINE),
        "python-full",
        kwargs.pop("mode", "enforce"),
        **kwargs,
    )


@pytest.mark.parametrize(
    ("expression", "approved"),
    [
        ("MIT", True),
        ("MIT OR GPL-3.0-only", True),
        ("MIT AND GPL-3.0-only", False),
        ("(MIT OR GPL-3.0-only) AND Apache-2.0", True),
        ("Apache-2.0 WITH LLVM-exception", True),
        ("MIT WITH made-up-exception", False),
        ("MIT OR", False),
        ("MIT OR (GPL", False),
        ("MIT garbage", False),
        ("MIT/Proprietary", False),
        ("", False),
        ("MIT WITH OR", False),
    ],
)
def test_spdx_expression(expression, approved):
    assert check.approved_expression(expression, set(POLICY["approved_expressions"])) is approved


def test_missing_metadata_is_a_finding_even_without_trivy_severity():
    result, failed = run_report(report())
    assert failed
    assert result["findings"][0]["issue"] == "missing-license"


def test_report_mode_reports_findings_without_blocking():
    result, failed = run_report(report(["LGPL-3.0-only"]), mode="report")
    assert result["pending"] == 1
    assert not failed


def test_known_permissive_license_passes():
    result, failed = run_report(report(["MIT"]))
    assert result["pending"] == 0
    assert not failed


def test_enforcement_requires_reviewed_baseline():
    with pytest.raises(ValueError, match="reviewed Linux baseline"):
        run_report(report(), baseline={"reviewed": False})


@pytest.mark.parametrize("data", [{}, {"SchemaVersion": 2, "Results": []}])
def test_incomplete_scan_fails_even_in_report_mode(data):
    with pytest.raises(ValueError):
        run_report(data, mode="report")


def test_independent_inventory_detects_analyzer_coverage_gap():
    with pytest.raises(ValueError, match="missing from Trivy"):
        run_report(
            report(["MIT"]), installed=[{"name": "unseen", "version": "1", "files": []}], mode="report"
        )


def test_missing_shipped_license_files_requires_review():
    result, failed = run_report(report(["MIT"]), installed=[{"name": "demo", "version": "1", "files": []}])
    assert failed
    assert result["findings"][0]["issue"] == "missing-license-files"


def test_reviewed_baseline_is_bound_to_version_scope_and_evidence():
    original = report(["LGPL-3.0-only"])
    installed = [{"name": "demo", "version": "1", "files": [{"path": "LICENSE", "sha256": "first"}]}]
    initial, _ = run_report(original, installed=installed)
    entry = {**initial["findings"][0], "reason": "Reviewed LGPL obligations", "reviewer": "maintainer"}
    baseline = {"reviewed": True, "findings": [entry]}
    assert not run_report(original, installed=installed, baseline=baseline)[1]
    assert run_report(report(["LGPL-3.0-only"], version="2"), baseline=baseline)[1]
    installed[0]["files"][0]["sha256"] = "changed"
    assert run_report(original, installed=installed, baseline=baseline)[1]
    assert check.evaluate(original, [], POLICY, baseline, "python-slim", "enforce")[1]


def test_expired_exception_and_new_version_require_review():
    policy = {
        **POLICY,
        "exceptions": [
            {
                "name": "demo",
                "version": "1",
                "scope": "python-full",
                "issue": "missing-license",
                "reason": "Owner accepted",
                "reviewer": "owner",
                "expires": "2026-10-16",
            }
        ],
    }
    assert not run_report(report(), policy=policy, today=date(2026, 10, 16))[1]
    assert run_report(report(), policy=policy, today=date(2026, 10, 17))[1]
    assert run_report(report(version="2"), policy=policy, today=date(2026, 10, 16))[1]


def test_forbidden_dependency_fails_during_report_rollout():
    policy = {**POLICY, "forbidden_packages": {"python-full": ["udapi"]}}
    with pytest.raises(ValueError, match="Forbidden package reintroduced"):
        run_report(report(["GPL-3.0-only"], name="udapi"), policy=policy, mode="report")


def test_python_inventory_reads_actual_wheel_metadata_and_license(tmp_path):
    dist = tmp_path / "lib/python3.12/site-packages/demo-1.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text("Name: demo\nVersion: 1\n")
    (dist / "LICENSE").write_text("license contents")
    records = check.inventory("python", tmp_path)
    assert records[0]["name"] == "demo"
    assert len(records[0]["files"][0]["sha256"]) == 64


def test_node_inventory_includes_scoped_packages(tmp_path):
    pkg = tmp_path / ".pnpm/scope+demo@1/node_modules/@scope/demo"
    pkg.mkdir(parents=True)
    (pkg / "package.json").write_text(json.dumps({"name": "@scope/demo", "version": "1"}))
    assert check.inventory("node", tmp_path)[0]["name"] == "@scope/demo"


def test_bundled_license_text_is_reviewed_separately():
    data = report(["MIT"])
    data["Results"].append(
        {
            "Type": "license-file",
            "Target": "third_party/LICENSE",
            "Licenses": [{"Name": "GPL-3.0-only", "FilePath": "third_party/LICENSE"}],
        }
    )
    proof = {"third_party/LICENSE": {"path": "third_party/LICENSE", "sha256": "first"}}
    result, failed = run_report(data, bundled_evidence=proof)
    assert failed
    assert result["findings"][0]["issue"] == "bundled-license"
    baseline = {
        "reviewed": True,
        "findings": [
            {**result["findings"][0], "reason": "Reviewed copied license", "reviewer": "maintainer"}
        ],
    }
    assert not run_report(data, bundled_evidence=proof, baseline=baseline)[1]
    proof["third_party/LICENSE"]["sha256"] = "changed"
    assert run_report(data, bundled_evidence=proof, baseline=baseline)[1]


def make_archives(tmp_path, notice=b"notice"):
    root = tmp_path / "source"
    root.mkdir()
    (root / "LICENSE").write_bytes(b"license")
    (root / "NOTICE").write_bytes(b"notice")
    dist = tmp_path / "dist"
    dist.mkdir()
    metadata = b"License-File: LICENSE\nLicense-File: NOTICE\n"
    with zipfile.ZipFile(dist / "cuga-1.whl", "w") as archive:
        archive.writestr("cuga-1.dist-info/METADATA", metadata)
        archive.writestr("cuga-1.dist-info/licenses/LICENSE", b"license")
        archive.writestr("cuga-1.dist-info/licenses/NOTICE", notice)
    with tarfile.open(dist / "cuga-1.tar.gz", "w:gz") as archive:
        for name, content in {"PKG-INFO": metadata, "LICENSE": b"license", "NOTICE": b"notice"}.items():
            member = tarfile.TarInfo(f"cuga-1/{name}")
            member.size = len(content)
            archive.addfile(member, BytesIO(content))
    return dist, root


def test_both_archives_preserve_notices(tmp_path):
    check.verify_archives(*make_archives(tmp_path))


def test_changed_notice_blocks_publication(tmp_path):
    with pytest.raises(ValueError, match="mismatched NOTICE"):
        check.verify_archives(*make_archives(tmp_path, notice=b"changed"))


def descriptors():
    return {
        f"{profile}-{arch}": "ghcr.io/cuga-project/cuga@sha256:" + str(index) * 64
        for index, (profile, arch) in enumerate(
            (("full", "amd64"), ("full", "arm64"), ("slim", "amd64"), ("slim", "arm64")), 1
        )
    }


def test_promotion_reuses_scanned_digests_for_full_and_slim():
    commands = promote.promotion_commands("ghcr.io/cuga-project/cuga", "tag", "v1.2.3", "", descriptors())
    assert len(commands) == 2
    for command, suffix in zip(commands, ("", "-slim")):
        actual_tags = {command[index + 1] for index, arg in enumerate(command) if arg == "--tag"}
        assert actual_tags == {
            f"ghcr.io/cuga-project/cuga:{tag}{suffix}" for tag in ("1.2.3", "1.2", "1", "latest")
        }
    assert commands[0][-2:] == [descriptors()[f"full-{arch}"] for arch in ("amd64", "arm64")]


def test_promotion_validates_all_images_before_publishing_any_tag():
    values = descriptors()
    values["slim-arm64"] = "ghcr.io/other/image@sha256:" + "a" * 64
    with pytest.raises(ValueError, match="scanned image digest"):
        promote.promotion_commands("ghcr.io/cuga-project/cuga", "tag", "v1.2.3", "", values)


def test_manual_image_tag_is_literal_and_validated():
    with pytest.raises(ValueError, match="Invalid image tag"):
        promote.promotion_commands("ghcr.io/cuga-project/cuga", "branch", "main", "$(bad)", descriptors())


def test_python_inventory_preserves_license_files_shipped_outside_dist_info(tmp_path):
    site = tmp_path / "lib/python3.12/site-packages"
    dist = site / "demo-1.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text("Name: demo\nVersion: 1\n")
    package = site / "demo/licenses"
    package.mkdir(parents=True)
    (package / "LICENSE").write_text("native dependency license")
    (dist / "RECORD").write_text("demo/licenses/LICENSE,,\n")
    assert check.inventory("python", tmp_path)[0]["files"][0]["path"] == "demo/licenses/LICENSE"


@pytest.mark.parametrize("profile", ["full", "slim"])
def test_actual_dockerfile_profile_is_parseable_and_frozen(profile):
    args = check.profile_arguments(profile, ROOT)
    assert "--frozen" in args
    assert "--no-editable" in args
    excluded = {args[i + 1] for i, value in enumerate(args) if value == "--no-install-package"}
    if profile == "full":
        assert {"stanza", "udtools", "udapi"} <= excluded
    else:
        assert {"torch", "docling", "saxonche", "doclang"} <= excluded
        assert len(excluded) > 30


def test_bundled_file_evidence_hashes_exact_content_and_rejects_paths_outside_root(tmp_path):
    (tmp_path / "LICENSE").write_text("first")
    data = report(["MIT"])
    data["Results"].append({"Licenses": [{"Name": "GPL-3.0-only", "FilePath": "LICENSE"}]})
    first = check.collect_file_evidence(data, tmp_path)
    (tmp_path / "LICENSE").write_text("changed")
    assert first != check.collect_file_evidence(data, tmp_path)
    data["Results"][-1]["Licenses"][0]["FilePath"] = "../LICENSE"
    with pytest.raises(ValueError, match="Cannot read bundled"):
        check.collect_file_evidence(data, tmp_path)


def test_unproven_bundled_finding_cannot_be_baselined():
    data = report(["MIT"])
    data["Results"].append({"Licenses": [{"Name": "GPL-3.0-only", "FilePath": "LICENSE"}]})
    with pytest.raises(ValueError, match="Missing file evidence"):
        run_report(data, mode="report")


@pytest.mark.parametrize(("requested", "expected"), [("", "latest"), ("audit-test", "audit-test")])
def test_manual_branch_dispatch_retains_its_explicit_tag_set(requested, expected):
    commands = promote.promotion_commands(
        "ghcr.io/cuga-project/cuga", "branch", "main", requested, descriptors()
    )
    for command, suffix in zip(commands, ("", "-slim")):
        actual_tags = {command[index + 1] for index, arg in enumerate(command) if arg == "--tag"}
        assert actual_tags == {f"ghcr.io/cuga-project/cuga:{expected}{suffix}"}
