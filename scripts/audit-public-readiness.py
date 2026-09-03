from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

MAX_SCANNED_BLOB_BYTES = 5 * 1024 * 1024
TEXT_SUFFIXES = {
    ".cfg",
    ".conf",
    ".css",
    ".cjs",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".lock",
    ".md",
    ".properties",
    ".ps1",
    ".py",
    ".sh",
    ".svg",
    ".toml",
    ".tsv",
    ".txt",
    ".webmanifest",
    ".xml",
    ".yml",
    ".yaml",
}
FORBIDDEN_TRACKED_NAMES = {
    ".env",
    ".firebaserc",
    "config.toml",
    "firebase-tools.json",
    "credentials.json",
    "application_default_credentials.json",
    "antigravity-keyring.json",
    "oauth_creds.json",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".dev.vars",
    "id_rsa",
    "id_ed25519",
}
SAFE_EMAIL_DOMAINS = {"example.com", "github.com", "users.noreply.github.com"}
SAFE_AUTHOR_NAMES = {
    "Dario Novelli",
    "The Daily Nexus",
    "The Daily Nexus contributors",
}
SAFE_FIREBASE_PROJECTS = {
    "test",
    "safe-project",
    "daily-nexus-private",
    "daily-nexus-private-123",
    "example-private-project",
    "not-the-configured-project",
}
PUBLIC_OAUTH_VALUES = {
    "https://accounts.google.com/o/oauth2/auth",
    "https://oauth2.googleapis.com/token",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/oauth2/v1/certs",
}


@dataclass(frozen=True, slots=True)
class Finding:
    category: str
    location: str


def _git(*arguments: str, input_value: bytes | None = None) -> bytes:
    executable = shutil.which("git")
    if not executable:
        raise RuntimeError("Git is not installed")
    completed = subprocess.run(
        [executable, *arguments],
        input=input_value,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Git audit command failed: {' '.join(arguments[:2])}"
        )
    return completed.stdout


def _public_source_paths() -> list[Path]:
    raw = _git(
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
    )
    return [
        Path(item.decode("utf-8"))
        for item in raw.split(b"\0")
        if item
    ]


def _looks_textual(path: Path) -> bool:
    return path.suffix.casefold() in TEXT_SUFFIXES or path.name in {
        ".gitignore",
        "LICENSE",
    }


def _generic_findings(location: str, value: str) -> list[Finding]:
    findings: list[Finding] = []
    for match in re.finditer(
        r"(?<![A-Za-z0-9._%+-])"
        r"([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})",
        value,
    ):
        domain = match.group(2).casefold()
        if domain not in SAFE_EMAIL_DOMAINS and not domain.endswith(".invalid"):
            findings.append(Finding("personal email", location))
            break
    if re.search(r"AIza[0-9A-Za-z_-]{20,}", value):
        findings.append(Finding("Google/Firebase API key", location))
    if re.search(r"\b[0-9]+-[0-9A-Za-z_-]{20,}\.apps\.googleusercontent\.com\b", value):
        findings.append(Finding("Google OAuth client ID", location))
    if re.search(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", value):
        findings.append(Finding("private key", location))
    if re.search(r"\bGOCSPX-[0-9A-Za-z_-]{20,}\b", value) or re.search(
        r"\b(?:1//|ya29\.|ghp_|github_pat_)[0-9A-Za-z._/-]{20,}\b",
        value,
    ):
        findings.append(Finding("credential value", location))
    if re.search(r"\bAKIA[0-9A-Z]{16}\b", value):
        findings.append(Finding("AWS access key", location))
    if re.search(r"\bxox[baprs]-[0-9A-Za-z-]{20,}\b", value):
        findings.append(Finding("Slack credential", location))
    if re.search(r"\bsk_live_[0-9A-Za-z]{20,}\b", value):
        findings.append(Finding("live payment credential", location))
    if re.search(
        r"\beyJ[0-9A-Za-z_-]{12,}\.[0-9A-Za-z_-]{12,}\.[0-9A-Za-z_-]{12,}\b",
        value,
    ):
        findings.append(Finding("JSON web token", location))
    if re.search(
        r"[A-Za-z]:\\Users\\(?!<|%|YOUR|example)[A-Za-z0-9._-]{1,64}\\",
        value,
        flags=re.IGNORECASE,
    ) or re.search(
        r"/(?:Users|home)/(?!<|%|YOUR|example)[A-Za-z0-9._-]{1,64}/",
        value,
    ):
        findings.append(Finding("user-specific absolute path", location))
    for match in re.finditer(
        r"https://([a-z][a-z0-9-]{0,62})\.([a-z0-9-]{1,63})\.workers\.dev\b",
        value,
    ):
        if match.group(2) != "example":
            findings.append(Finding("deployment-specific Cloudflare Worker host", location))
            break
    for match in re.finditer(
        r"https://([a-z][a-z0-9-]{4,28}[a-z0-9])\."
        r"(?:web\.app|firebaseapp\.com)",
        value,
    ):
        if match.group(1) not in SAFE_FIREBASE_PROJECTS:
            findings.append(Finding("deployment-specific Firebase host", location))
            break
    return findings


def scan_current_tree() -> list[Finding]:
    findings: list[Finding] = []
    for path in _public_source_paths():
        normalized_name = path.name.casefold()
        if (
            normalized_name in FORBIDDEN_TRACKED_NAMES
            or normalized_name.startswith("client_secret")
            or normalized_name.startswith(("service-account", "service_account"))
            or "firebase-adminsdk" in normalized_name
            or normalized_name.endswith((".jks", ".kdbx", ".pem", ".p12", ".pfx"))
        ):
            findings.append(Finding("forbidden public credential file", path.as_posix()))
        findings.extend(_generic_findings(f"filename:{path.as_posix()}", path.as_posix()))
        try:
            raw = path.read_bytes()
        except OSError:
            findings.append(Finding("unreadable public source file", path.as_posix()))
            continue
        if len(raw) > MAX_SCANNED_BLOB_BYTES and _looks_textual(path):
            findings.append(Finding("oversized public source text file", path.as_posix()))
            continue
        if len(raw) > MAX_SCANNED_BLOB_BYTES:
            continue
        findings.extend(
            _generic_findings(
                path.as_posix(),
                raw.decode("utf-8", errors="replace"),
            )
        )
    return findings


def _history_blobs() -> Iterable[tuple[str, str, bytes]]:
    objects = _git("rev-list", "--objects", "--all").decode(
        "utf-8",
        errors="replace",
    )
    candidates: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line in objects.splitlines():
        object_id, _, path_value = line.partition(" ")
        if not path_value or object_id in seen:
            continue
        seen.add(object_id)
        candidates.append((object_id, Path(path_value).as_posix()))
    executable = shutil.which("git")
    if not executable:
        raise RuntimeError("Git is not installed")
    process = subprocess.Popen(
        [executable, "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if process.stdin is None or process.stdout is None:
        process.kill()
        raise RuntimeError("Git history audit could not open its private pipe")
    try:
        for object_id, path in candidates:
            process.stdin.write(f"{object_id}\n".encode())
            process.stdin.flush()
            header = process.stdout.readline().decode("ascii", errors="replace").strip()
            parts = header.split()
            if len(parts) != 3:
                raise RuntimeError("Git returned invalid object metadata")
            object_type = parts[1]
            size = int(parts[2])
            raw = process.stdout.read(size)
            process.stdout.read(1)
            if object_type == "blob" and size <= MAX_SCANNED_BLOB_BYTES:
                yield object_id, path, raw
    finally:
        process.stdin.close()
        process.wait(timeout=10)
    if process.returncode != 0:
        raise RuntimeError("Git history audit could not read repository objects")


def scan_history(*, strict_metadata: bool = False) -> list[Finding]:
    findings: list[Finding] = []
    for object_id, path, raw in _history_blobs():
        location = f"history:{object_id[:12]}:{path}"
        findings.extend(
            _generic_findings(
                location,
                raw.decode("utf-8", errors="replace"),
            )
        )
    log = _git("log", "--all", "--format=%H%x00%an%x00%ae%x00%cn%x00%ce").decode(
        "utf-8",
        errors="replace",
    )
    for line in log.splitlines():
        parts = line.split("\0")
        if len(parts) != 5:
            continue
        commit, author_name, author_email, committer_name, committer_email = parts
        for role, name, email in (
            ("author", author_name, author_email),
            ("committer", committer_name, committer_email),
        ):
            domain = email.rsplit("@", 1)[-1].casefold()
            if email and domain not in SAFE_EMAIL_DOMAINS:
                findings.append(
                    Finding(f"personal commit-{role} email", f"commit:{commit[:12]}")
                )
            if strict_metadata and name and name not in SAFE_AUTHOR_NAMES:
                findings.append(
                    Finding(
                        f"unexpected commit-{role} name",
                        f"commit:{commit[:12]}",
                    )
                )
    return findings


def _recursive_private_strings(value, *, prefix: str) -> Iterable[tuple[str, str]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _recursive_private_strings(item, prefix=f"{prefix}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _recursive_private_strings(item, prefix=f"{prefix}[{index}]")
    elif (
        isinstance(value, str)
        and len(value) >= 12
        and value not in PUBLIC_OAUTH_VALUES
        and not value.startswith("http://localhost")
    ):
        yield prefix, value


def _load_json(path: Path) -> dict:
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _windows_generic_credential_json(target: str) -> dict:
    if sys.platform != "win32":
        return {}
    try:
        import ctypes
        from ctypes import wintypes

        class Credential(ctypes.Structure):
            _fields_ = [
                ("flags", wintypes.DWORD),
                ("type", wintypes.DWORD),
                ("target_name", wintypes.LPWSTR),
                ("comment", wintypes.LPWSTR),
                ("last_written", wintypes.FILETIME),
                ("credential_blob_size", wintypes.DWORD),
                ("credential_blob", ctypes.POINTER(ctypes.c_ubyte)),
                ("persist", wintypes.DWORD),
                ("attribute_count", wintypes.DWORD),
                ("attributes", ctypes.c_void_p),
                ("target_alias", wintypes.LPWSTR),
                ("user_name", wintypes.LPWSTR),
            ]

        credential_pointer = ctypes.POINTER(Credential)()
        credential_api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        credential_api.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(Credential)),
        ]
        credential_api.CredReadW.restype = wintypes.BOOL
        credential_api.CredFree.argtypes = [ctypes.c_void_p]
        credential_api.CredFree.restype = None
        if not credential_api.CredReadW(
            target,
            1,
            0,
            ctypes.byref(credential_pointer),
        ):
            return {}
        try:
            credential = credential_pointer.contents
            raw = ctypes.string_at(
                credential.credential_blob,
                credential.credential_blob_size,
            )
            value = json.loads(raw.decode("utf-8"))
            return value if isinstance(value, dict) else {}
        finally:
            credential_api.CredFree(credential_pointer)
    except (OSError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return {}


def _local_private_values(config_path: Path) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    try:
        with config_path.open("rb") as handle:
            config = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return result
    for section, key in (
        ("firebase", "project_id"),
        ("firebase", "base_url"),
        ("firebase", "secret_path"),
        ("web", "firebase_api_key"),
        ("web", "owner_uid"),
    ):
        value = config.get(section, {}).get(key)
        if isinstance(value, str) and len(value) >= 6:
            result.append((f"config:{section}.{key}", value))
    for path in config_path.parent.glob("client_secret*.json"):
        for label, value in _recursive_private_strings(
            _load_json(path),
            prefix=f"file:{path.name}",
        ):
            result.append((label, value))
    antigravity = Path.home() / ".gemini" / "oauth_creds.json"
    for label, value in _recursive_private_strings(
        _load_json(antigravity),
        prefix="local:retired-antigravity-file",
    ):
        result.append((label, value))
    for label, value in _recursive_private_strings(
        _windows_generic_credential_json("gemini:antigravity"),
        prefix="local:antigravity-keyring",
    ):
        result.append((label, value))
    firebase_cli = Path.home() / ".config" / "configstore" / "firebase-tools.json"
    firebase_data = _load_json(firebase_cli).get("tokens", {})
    for label, value in _recursive_private_strings(
        firebase_data,
        prefix="local:firebase-cli",
    ):
        result.append((label, value))
    try:
        source_root = config_path.parent / "src"
        if source_root.is_dir():
            sys.path.insert(0, str(source_root))
        from audiodigest.config import load_settings
        from audiodigest.gmail_client import GmailTokenStore
        from audiodigest.web_runner import WebRunnerTokenStore

        settings = load_settings(config_path)
        for label, value in (
            ("local:gmail-token", GmailTokenStore(settings).get()),
            (
                "local:gmail-account-email",
                GmailTokenStore(settings).get_account_email(),
            ),
            ("local:firebase-runner-token", WebRunnerTokenStore(settings).get()),
        ):
            if isinstance(value, str) and len(value) >= 6:
                result.append((label, value))
    except (ImportError, OSError, RuntimeError, ValueError):
        # Generic scanning still runs when the optional local runtime is absent.
        pass
    return result


def scan_exact_local_values(config_path: Path, *, history: bool) -> list[Finding]:
    private_values = _local_private_values(config_path)
    if not private_values:
        return []
    candidates: list[tuple[str, bytes]] = []
    for path in _public_source_paths():
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if len(raw) <= MAX_SCANNED_BLOB_BYTES:
            candidates.append((path.as_posix(), raw))
    if history:
        candidates.extend(
            (f"history:{object_id[:12]}:{path}", raw)
            for object_id, path, raw in _history_blobs()
        )
    findings: list[Finding] = []
    for private_label, private_value in private_values:
        needle = private_value.encode("utf-8")
        if any(needle in raw for _, raw in candidates):
            findings.append(Finding(f"local private value ({private_label})", "Git data"))
    return findings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="audit-public-readiness")
    parser.add_argument("--history", action="store_true")
    parser.add_argument(
        "--strict-metadata",
        action="store_true",
        help="allow only neutral project identities in commit metadata",
    )
    parser.add_argument("--local-config", type=Path)
    args = parser.parse_args(argv)
    if args.strict_metadata and not args.history:
        parser.error("--strict-metadata requires --history")
    findings = scan_current_tree()
    if args.history:
        findings.extend(scan_history(strict_metadata=args.strict_metadata))
    if args.local_config:
        findings.extend(
            scan_exact_local_values(
                args.local_config.resolve(),
                history=args.history,
            )
        )
    unique = sorted(
        {(item.category, item.location) for item in findings},
        key=lambda item: (item[0], item[1]),
    )
    if unique:
        for category, location in unique:
            print(f"FAIL {category}: {location}")
        print(f"Public-readiness audit failed with {len(unique)} finding(s).")
        raise SystemExit(1)
    scope = "current public source and history" if args.history else "current public source"
    print(f"Public-readiness audit passed for {scope}.")


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as exc:
        print(f"Audit could not complete: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
