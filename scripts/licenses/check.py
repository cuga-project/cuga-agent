"""Collect license evidence and apply the reviewed policy to Trivy JSON (stdlib only)."""

import argparse
import csv
import email
import hashlib
import json
import os
import re
import shlex
import subprocess
import tarfile
import zipfile
from datetime import date
from pathlib import Path


def normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def approved_expression(expression, approved):
    """Evaluate SPDX AND/OR/WITH without treating an exception as a separate license."""
    tokens = re.findall(r"\(|\)|[A-Za-z0-9.+:_-]+", expression)
    if "".join(tokens) != re.sub(r"\s+", "", expression):
        return False
    pos = 0

    def atom():
        nonlocal pos
        if pos >= len(tokens):
            raise ValueError("Incomplete SPDX expression")
        token = tokens[pos]
        pos += 1
        if token == "(":
            value = either()
            if pos >= len(tokens) or tokens[pos] != ")":
                raise ValueError("Unclosed SPDX expression")
            pos += 1
            return value
        if token in {"AND", "OR", "WITH", ")"}:
            raise ValueError("Unexpected SPDX operator")
        if pos < len(tokens) and tokens[pos] == "WITH":
            pos += 1
            if pos >= len(tokens) or tokens[pos] in {"AND", "OR", "WITH", "(", ")"}:
                raise ValueError("Missing SPDX exception")
            token += " WITH " + tokens[pos]
            pos += 1
        return token in approved

    def both():
        nonlocal pos
        value = atom()
        while pos < len(tokens) and tokens[pos] == "AND":
            pos += 1
            right = atom()
            value = value and right
        return value

    def either():
        nonlocal pos
        value = both()
        while pos < len(tokens) and tokens[pos] == "OR":
            pos += 1
            right = both()
            value = value or right
        return value

    try:
        value = either()
        return value and pos == len(tokens)
    except ValueError:
        return False


def evidence_files(root):
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")):
            files.append(
                {"path": str(path.relative_to(root)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
            )
    return files


def inventory(kind, root):
    records = []
    if kind == "python":
        for dist in sorted(root.glob("lib/python*/site-packages/*.dist-info")):
            metadata = email.message_from_string((dist / "METADATA").read_text())
            proof = {item["path"]: item for item in evidence_files(dist)}
            record = dist / "RECORD"
            if record.exists():
                for row in csv.reader(record.read_text().splitlines()):
                    candidate = (dist.parent / row[0]).resolve()
                    if (
                        candidate.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE"))
                        and candidate.is_file()
                        and candidate.is_relative_to(root.resolve())
                    ):
                        key = str(candidate.relative_to(dist.parent.resolve()))
                        proof[key] = {
                            "path": key,
                            "sha256": hashlib.sha256(candidate.read_bytes()).hexdigest(),
                        }
            records.append(
                {
                    "name": metadata["Name"],
                    "version": metadata["Version"],
                    "files": sorted(proof.values(), key=lambda item: item["path"]),
                }
            )
    else:
        paths = list(root.glob(".pnpm/*/node_modules/*/package.json"))
        paths += list(root.glob(".pnpm/*/node_modules/@*/*/package.json"))
        seen = set()
        for path in sorted(paths):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            pkg = read_json(path)
            if pkg.get("name") and pkg.get("version"):
                records.append(
                    {"name": pkg["name"], "version": pkg["version"], "files": evidence_files(path.parent)}
                )
    if not records:
        raise ValueError(f"Empty {kind} installed inventory: {root}")
    return records


def finding(scope, ecosystem, name, version, issue, licenses, evidence=None):
    value = {
        "scope": scope,
        "ecosystem": ecosystem,
        "name": name,
        "version": version,
        "issue": issue,
        "licenses": sorted(licenses),
        "evidence": evidence or [],
    }
    value["fingerprint"] = hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
    return value


def evaluate(report, installed, policy, baseline, scope, mode, today=None, bundled_evidence=None):
    today = today or date.today()
    if report.get("SchemaVersion") != 2 or not isinstance(report.get("Results"), list):
        raise ValueError("Invalid or incomplete Trivy JSON report")
    if mode == "enforce" and not baseline.get("reviewed"):
        raise ValueError("Enforcement requires a reviewed Linux baseline")
    approved = set(policy["approved_expressions"])
    records = []
    scanned = set()
    evidence = {(normalize(p["name"]), p["version"]): p["files"] for p in installed}
    for result in report["Results"]:
        ecosystem = result.get("Type", "unknown")
        for pkg in result.get("Packages", []):
            name, version = pkg["Name"], pkg["Version"]
            scanned.add((normalize(name), version))
            forbidden = policy.get("forbidden_packages", {}).get(scope, [])
            if normalize(name) in {normalize(item) for item in forbidden}:
                raise ValueError(f"Forbidden package reintroduced in {scope}: {name}=={version}")
            licenses = pkg.get("Licenses") or []
            proof = evidence.get((normalize(name), version), [])
            if not licenses:
                records.append(finding(scope, ecosystem, name, version, "missing-license", [], proof))
            elif not all(approved_expression(item, approved) for item in licenses):
                records.append(
                    finding(scope, ecosystem, name, version, "unreviewed-license", licenses, proof)
                )
        # Full scans also find bundled/source license texts without package metadata.
        for item in result.get("Licenses", []):
            if item.get("PkgName"):
                continue  # package metadata above is authoritative and includes empty licenses
            license_name = item["Name"]
            if not approved_expression(license_name, approved):
                path = item.get("FilePath") or result.get("Target", "")
                path = re.sub(r"^.*?/site-packages/", "site-packages/", path)
                proof = (bundled_evidence or {}).get(item.get("FilePath", ""))
                if not proof:
                    raise ValueError(f"Missing file evidence for bundled license: {path}")
                records.append(
                    finding(scope, ecosystem, path, "", "bundled-license", [license_name], [proof])
                )
    if not scanned:
        raise ValueError("Trivy report contains no package inventory")
    missing = sorted(set(evidence) - scanned)
    if missing:
        raise ValueError(f"Installed packages missing from Trivy inventory: {missing}")
    for pkg in installed:
        if not pkg["files"]:
            records.append(
                finding(scope, "installed", pkg["name"], pkg["version"], "missing-license-files", [])
            )
    accepted = {}
    for entry in baseline.get("findings", []):
        if not entry.get("reason") or not entry.get("reviewer") or not entry.get("fingerprint"):
            raise ValueError("Baseline findings need fingerprint, reason and reviewer")
        if not entry.get("expires") or date.fromisoformat(entry["expires"]) >= today:
            accepted[entry["fingerprint"]] = entry
    exceptions = policy.get("exceptions", [])
    for entry in exceptions:
        if not all(
            entry.get(k) for k in ("name", "version", "scope", "issue", "reason", "reviewer", "expires")
        ):
            raise ValueError("Exceptions must be version-scoped and include review and expiry")
        date.fromisoformat(entry["expires"])
    unique = {item["fingerprint"]: item for item in records}
    for item in unique.values():
        item["accepted"] = item["fingerprint"] in accepted or any(
            normalize(e["name"]) == normalize(item["name"])
            and e["version"] == item["version"]
            and e["scope"] in {"*", scope}
            and e["issue"] == item["issue"]
            and date.fromisoformat(e["expires"]) >= today
            for e in exceptions
        )
    findings = sorted(unique.values(), key=lambda p: (p["name"], p["version"], p["issue"]))
    pending = sum(not item["accepted"] for item in findings)
    return {
        "scope": scope,
        "mode": mode,
        "packages": len(scanned),
        "installed": len(installed),
        "pending": pending,
        "findings": findings,
    }, mode == "enforce" and pending > 0


def collect_file_evidence(report, root):
    root = root.resolve()
    records = {}
    for result in report.get("Results", []):
        for item in result.get("Licenses", []):
            if item.get("PkgName"):
                continue
            name = item.get("FilePath", "")
            if not name:
                raise ValueError("Bundled license finding has no file path")
            path = (root / name.lstrip("/")).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                raise ValueError(f"Cannot read bundled license evidence: {name}")
            records[name] = {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return records


def profile_arguments(profile, root=Path(".")):
    dockerfile = root / ("Dockerfile.ubi" if profile == "full" else "Dockerfile.ubi.slim")
    text = "\n".join(
        line for line in dockerfile.read_text().splitlines() if not line.lstrip().startswith("#")
    )
    text = text.replace("\\\n", " ")
    matches = re.findall(r"\buv sync\s+([^\n]+)", text)
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one uv sync in {dockerfile}")
    args = shlex.split(matches[0])
    if any(re.search(r"[;&|`$<>]", arg) for arg in args) or "--frozen" not in args:
        raise ValueError("Image uv sync must contain only frozen uv arguments")
    return args


def sync_profile(profile, environment):
    subprocess.run(
        ["uv", "sync", *profile_arguments(profile)],
        check=True,
        env={**os.environ, "UV_PROJECT_ENVIRONMENT": str(environment.resolve())},
    )


def verify_archives(directory, root):
    archives = sorted(directory.glob("*.whl")) + sorted(directory.glob("*.tar.gz"))
    if len(archives) != 2 or not any(p.suffix == ".whl" for p in archives):
        raise ValueError("Expected exactly one wheel and one sdist")
    for path in archives:
        if path.suffix == ".whl":
            with zipfile.ZipFile(path) as archive:
                files = {name: archive.read(name) for name in archive.namelist() if not name.endswith("/")}
            metadata = next(value for name, value in files.items() if name.endswith(".dist-info/METADATA"))
        else:
            with tarfile.open(path) as archive:
                files = {
                    member.name: archive.extractfile(member).read()
                    for member in archive.getmembers()
                    if member.isfile()
                }
            metadata = next(
                value for name, value in files.items() if name.count("/") == 1 and name.endswith("/PKG-INFO")
            )
        for name in ("LICENSE", "NOTICE"):
            expected = (root / name).read_bytes()
            matches = [
                value
                for key, value in files.items()
                if key.endswith("/" + name)
                and (".dist-info/licenses/" in key if path.suffix == ".whl" else key.count("/") == 1)
            ]
            if (
                not expected.strip()
                or matches != [expected]
                or f"License-File: {name}".encode() not in metadata
            ):
                raise ValueError(f"{path.name}: missing or mismatched {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inv = sub.add_parser("inventory")
    inv.add_argument("kind", choices=["python", "node"])
    inv.add_argument("root", type=Path)
    inv.add_argument("output", type=Path)
    file_evidence = sub.add_parser("file-evidence")
    file_evidence.add_argument("report", type=Path)
    file_evidence.add_argument("root", type=Path)
    file_evidence.add_argument("output", type=Path)
    prepare = sub.add_parser("prepare-python")
    prepare.add_argument("profile", choices=["full", "slim"])
    prepare.add_argument("environment", type=Path)
    archives = sub.add_parser("archives")
    archives.add_argument("directory", type=Path)
    check = sub.add_parser("evaluate")
    check.add_argument("--report", required=True)
    check.add_argument("--inventory")
    check.add_argument("--file-evidence")
    check.add_argument("--policy-dir", default=".github/licenses")
    check.add_argument("--scope", required=True)
    check.add_argument("--mode", choices=["report", "enforce"], default="report")
    check.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "inventory":
        records = inventory(args.kind, args.root)
        if str(args.output) == "-":
            print(json.dumps(records))
        else:
            write_json(args.output, records)
    elif args.command == "file-evidence":
        records = collect_file_evidence(read_json(args.report), args.root)
        if str(args.output) == "-":
            print(json.dumps(records))
        else:
            write_json(args.output, records)
    elif args.command == "prepare-python":
        sync_profile(args.profile, args.environment)
    elif args.command == "archives":
        verify_archives(args.directory, Path.cwd())
    else:
        result, failed = evaluate(
            read_json(args.report),
            read_json(args.inventory) if args.inventory else [],
            read_json(Path(args.policy_dir) / "policy.json"),
            read_json(Path(args.policy_dir) / "baseline.json"),
            args.scope,
            args.mode,
            bundled_evidence=read_json(args.file_evidence) if args.file_evidence else None,
        )
        write_json(args.output, result)
        summary = f"License scan {args.scope}: {result['packages']} packages; {result['pending']} findings awaiting review ({args.mode}).\n"
        print(summary)
        if os.getenv("GITHUB_STEP_SUMMARY"):
            with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as stream:
                stream.write(summary + "\nSee the uploaded JSON reports for component-level evidence.\n")
        raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
