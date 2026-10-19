# License CI

The `Licenses` workflow runs on every PR, including forks, without secrets or image builds. It installs the full and slim Python dependency profiles on Linux using the actual `uv sync` arguments in their Dockerfiles. Frozen frontend installation includes build dependencies and disables lifecycle scripts. Independent installed inventories are compared with Trivy's package inventory; empty license metadata is explicitly reported even when Trivy emits no license finding. Wheel and sdist LICENSE/NOTICE content and metadata are checked separately.

Trivy 0.76.0 is pinned and downloaded from the official release with committed architecture-specific SHA-256 checksums. Its severity labels are evidence, not the decision policy. The evaluator understands SPDX AND, OR and WITH. Reciprocal, restricted, unknown and missing licenses require review. License files are hashed so accepted findings cannot silently survive changed evidence. Bundled source/license text findings need component-level review: a copied GPL license does not make an entire dependency GPL.

## Rollout

1. The image-license packaging prerequisites from PR #977 are already merged into main. This PR adds the CI checks on top of those changes.
2. Merge this workflow with the default `LICENSE_ENFORCEMENT=report`. All findings are visible in job summaries and uploaded JSON artifacts. Scan failures, missing inventory coverage, notice regressions, and reintroduced Stanza/udtools/udapi fail even in report mode.
3. Inspect the first Linux results for `python-full`, `python-slim` and `frontend-build-and-runtime`, plus manual full/slim image results for both architectures. The local macOS report is not the Linux baseline.
4. Review each pending finding. Correct metadata errors with evidence rather than declaring whole packages GPL or proprietary. Known cases include Docutils, fastembed, PyGithub/python-bidi LGPL versions, OFL/BlueOak identifiers, and WTFPL license-policy classifications. SaxonC-HE missing licensing/source-availability material remains unresolved; do not approve it merely to silence CI. Verify the remaining LGPL obligations. The user's cuga-oak-health 1.3.0 acceptance is an explicit expiring exception, without inventing a license identifier.
5. For an accepted finding, copy its fields from `policy-result.json` into `.github/licenses/baseline.json` under `findings`, adding `reason`, `reviewer`, and optionally `expires` (ISO date). Retain its fingerprint unchanged; version, scope, license and evidence changes produce a different fingerprint. Avoid baselining findings where a fix is required. Set `reviewed: true` only after the baseline review. Use a maintainer-reviewed PR.
6. Set the repository Actions variable `LICENSE_ENFORCEMENT` to `enforce`. This blocks new/unreviewed findings; known permissive expressions pass automatically. Setting enforcement before a reviewed baseline fails closed.
7. Require the always-present `license-check` context in the main branch ruleset. Enable required CODEOWNERS review and dismiss stale approvals for policy, baseline, scanner and workflow changes. CODEOWNERS alone does not enforce review.

PR jobs read policy/baseline from the PR's base commit so changes cannot approve their own findings. The first introduction may use the PR's policy only in report mode when no trusted policy exists yet. A policy update takes effect on subsequent PRs after merging. Approve a new dependency in a separate policy PR first. If an expired exception already blocks all PRs, a maintainer must review the policy-only change and use the configured ruleset bypass to merge it; do not disable the checks globally. Do not use `pull_request_target` for dependency installation or scans.

## Release and manual images

`Deploy Image to GHCR` builds full/slim images separately on native Linux amd64/arm64 runners, scans each locally with `--license-full`, then pushes the same built image to a run-specific staging tag. Only after all four succeed does it assemble full and `-slim` release manifests from those exact digests. No image rebuild happens after scanning. Each architecture digest receives a GitHub build-provenance attestation before promotion. Existing full-image tag behavior is preserved; slim releases add matching `-slim` tags. Staging tags may need periodic registry retention cleanup.

The IBM manual deployment scans its local built image before deployment. PyPI publication verifies the exact wheel/sdist notices before `uv publish`.

During the report rollout, publishing is **not license clearance**: pending findings do not block publication yet. In enforce mode, reviewed policy is applied before images are pushed/promoted. OS licenses and bundled Chromium/native/model materials must be reviewed in their distribution context; do not apply a blanket GPL ban to OS packages. Model-weight provenance and source-offer obligations still require human review: Trivy cannot prove them from metadata. Native ARM runners must be available to the repository; image/model builds require substantially more disk than ordinary PR jobs.

## Local checks

```sh
uv run --isolated --no-project --with pytest==8.4.2 pytest --noconftest -c /dev/null -o "markers=unit: isolated unit tests" tests/unit/test_license_ci.py
uv build
python3 scripts/licenses/check.py archives dist
python3 scripts/licenses/check.py prepare-python full /tmp/cuga-license-full
python3 scripts/licenses/check.py inventory python /tmp/cuga-license-full license-reports/inventory.json
trivy rootfs --scanners license --list-all-pkgs --format json --output license-reports/trivy.json /tmp/cuga-license-full
python3 scripts/licenses/check.py evaluate --report license-reports/trivy.json --inventory license-reports/inventory.json --scope python-full --mode report --output license-reports/policy-result.json
```

Upload artifacts retain the raw scanner JSON, independent inventory and policy decisions. A successful report-mode run means the scans completed; it is not a compliance approval. The evaluator and policy regression tests use only the Python standard library plus pytest for tests.
