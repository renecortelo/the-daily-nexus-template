from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path


class PrivateStoreError(RuntimeError):
    """Raised when a configured private file cannot be handled safely."""


MAX_PRIVATE_VALUE_BYTES = 2 * 1024 * 1024
_POSIX = os.name == "posix"


def _assert_no_link_components(path: Path) -> None:
    # Do not resolve first: that would hide the link we must reject. This also
    # covers linked ancestors and Windows directory junctions, not only the leaf.
    for component in (path, *path.parents):
        if component.is_symlink() or (
            hasattr(component, "is_junction") and component.is_junction()
        ):
            raise PrivateStoreError("private file paths must not traverse links or junctions")


def _assert_single_regular_file(info: os.stat_result) -> None:
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise PrivateStoreError("private file must be regular and have no hard-link aliases")


def _assert_owner_only_file(info: os.stat_result) -> None:
    _assert_single_regular_file(info)
    if _POSIX and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise PrivateStoreError("private file must be owned by the current user with mode 0600")


def _assert_regular_private_path(path: Path) -> None:
    _assert_no_link_components(path)
    try:
        _assert_single_regular_file(path.lstat())
    except FileNotFoundError:
        pass


def assert_private_directory(path: Path) -> None:
    """Validate a managed directory without following links or creating it."""
    _assert_no_link_components(path)
    if path.exists() and not path.is_dir():
        raise PrivateStoreError("private directory path is not a directory")


def read_private_value(path: Path) -> str | None:
    _assert_regular_private_path(path)
    try:
        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) |
                 getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            _assert_owner_only_file(info)
            if info.st_size > MAX_PRIVATE_VALUE_BYTES:
                raise PrivateStoreError("private credential file exceeds the safety limit")
            raw = handle.read(MAX_PRIVATE_VALUE_BYTES + 1)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PrivateStoreError(f"could not read the private credential file: {path}") from exc
    if not raw or len(raw) > MAX_PRIVATE_VALUE_BYTES:
        raise PrivateStoreError("private credential file is empty or exceeds the safety limit")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PrivateStoreError("private credential file is not UTF-8") from exc


def write_private_value(
    path: Path, value: str, *, maximum_bytes: int = MAX_PRIVATE_VALUE_BYTES,
) -> None:
    if isinstance(maximum_bytes, bool) or not isinstance(maximum_bytes, int) or not (
        1 <= maximum_bytes <= 16 * 1024 * 1024
    ):
        raise PrivateStoreError("private file limit must be between 1 byte and 16 MiB")
    if not isinstance(value, str) or not value:
        raise PrivateStoreError("private credential value must not be empty")
    encoded = value.encode("utf-8")
    if len(encoded) > maximum_bytes:
        raise PrivateStoreError("private credential value exceeds the safety limit")
    _assert_regular_private_path(path)
    assert_private_directory(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    descriptor: int | None = None
    try:
        if _POSIX:
            path.parent.chmod(0o700)
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            if _POSIX:
                os.fchmod(handle.fileno(), 0o600)
            _assert_owner_only_file(os.fstat(handle.fileno()))
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        _assert_regular_private_path(path)
        os.replace(temporary, path)
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
