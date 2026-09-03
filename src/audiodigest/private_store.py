from __future__ import annotations

import os
import secrets
from pathlib import Path


class PrivateStoreError(RuntimeError):
    """Raised when a configured private file cannot be handled safely."""


MAX_PRIVATE_VALUE_BYTES = 2 * 1024 * 1024


def _assert_regular_private_path(path: Path) -> None:
    if path.is_symlink():
        raise PrivateStoreError(f"refusing a symbolic-link credential file: {path}")
    if path.exists() and not path.is_file():
        raise PrivateStoreError(f"credential path is not a regular file: {path}")


def read_private_value(path: Path) -> str | None:
    _assert_regular_private_path(path)
    if not path.exists():
        return None
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise PrivateStoreError(f"could not read the private credential file: {path}") from exc
    if not raw or len(raw) > MAX_PRIVATE_VALUE_BYTES:
        raise PrivateStoreError("private credential file is empty or exceeds the safety limit")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PrivateStoreError("private credential file is not UTF-8") from exc


def write_private_value(path: Path, value: str) -> None:
    if not isinstance(value, str) or not value:
        raise PrivateStoreError("private credential value must not be empty")
    encoded = value.encode("utf-8")
    if len(encoded) > MAX_PRIVATE_VALUE_BYTES:
        raise PrivateStoreError("private credential value exceeds the safety limit")
    _assert_regular_private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink():
        raise PrivateStoreError("credential directory must not be a symbolic link")
    try:
        path.parent.chmod(0o700)
    except OSError:
        # Windows protects this path with the user's ACL. POSIX runners also
        # receive the explicit owner-only mode above.
        pass
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except OSError as exc:
        raise PrivateStoreError(f"could not write the private credential file: {path}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def delete_private_value(path: Path) -> bool:
    _assert_regular_private_path(path)
    if not path.exists():
        return False
    try:
        path.unlink()
    except OSError as exc:
        raise PrivateStoreError(f"could not remove the private credential file: {path}") from exc
    return True
