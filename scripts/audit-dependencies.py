"""Audit every public Python version in uv.lock without installing the audio stack.

Only public package names and versions are sent to OSV. No configuration,
environment variables, user identifiers, credentials or source content are read.
An unavailable or malformed vulnerability service fails closed.
"""

from __future__ import annotations

import json
import sys
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

OSV_URL = "https://api.osv.dev/v1/querybatch"


def locked_packages(lock_path: Path) -> list[tuple[str, str]]:
    with lock_path.open("rb") as handle:
        lock = tomllib.load(handle)
    packages = set()
    for package in lock["package"]:
        source = package.get("source", {})
        registry = source.get("registry", "")
        if not registry:
            if "editable" in source:
                continue
            expected_model = (
                "https://github.com/explosion/spacy-models/releases/download/"
                "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
            )
            if (package["name"], package["version"], source.get("url")) != (
                "en-core-web-sm", "3.8.0", expected_model,
            ):
                raise ValueError("Unexpected non-registry dependency in lock")
        elif registry not in {"https://pypi.org/simple", "https://download.pytorch.org/whl/cpu"}:
            raise ValueError("Unexpected package registry in lock")
        # The CPU wheel is the same PyTorch source release for advisory matching.
        version = package["version"]
        if package["name"] == "torch":
            version = version.removesuffix("+cpu")
        packages.add((package["name"], version))
    if not packages:
        raise ValueError("Dependency lock is empty")
    return sorted(packages)


def audit(packages: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
    findings = []
    for offset in range(0, len(packages), 100):
        batch = packages[offset:offset + 100]
        payload = {"queries": [
            {"package": {"name": name, "ecosystem": "PyPI"}, "version": version}
            for name, version in batch
        ]}
        request = urllib.request.Request(
            OSV_URL, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(request, timeout=45) as response:  # noqa: S310
            result = json.load(response)
        results = result.get("results")
        if not isinstance(results, list) or len(results) != len(batch):
            raise ValueError("Malformed vulnerability response")
        for (name, version), entry in zip(batch, results, strict=True):
            if not isinstance(entry, dict) or "next_page_token" in entry:
                raise ValueError("Incomplete vulnerability response")
            for vulnerability in entry.get("vulns", []):
                findings.append((name, version, vulnerability["id"]))
    return findings


def main() -> int:
    try:
        packages = locked_packages(Path(__file__).resolve().parents[1] / "uv.lock")
        findings = audit(packages)
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
        print("Dependency audit unavailable or invalid; check not passed.", file=sys.stderr)
        return 2
    for name, version, advisory in findings:
        print(f"{name} {version}: {advisory}")
    print(f"Audited {len(packages)} locked Python releases; {len(findings)} advisory matches.")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
