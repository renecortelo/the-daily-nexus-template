import os
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from audiodigest.config import load_settings
from audiodigest.gmail_client import GmailTokenStore
from audiodigest.private_store import (
    PrivateStoreError,
    _assert_owner_only_file,
    delete_private_value,
    read_private_value,
    write_private_value,
)
from audiodigest.web_runner import WebRunnerTokenStore


class PrivateStoreTests(TestCase):
    def test_read_rejects_oversized_or_invalid_utf8_without_returning_data(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "secret"
            for raw in (b"", b"\xff", b"x" * (2 * 1024 * 1024 + 1)):
                path.write_bytes(raw)
                path.chmod(0o600)
                with self.subTest(length=len(raw)), self.assertRaises(PrivateStoreError):
                    read_private_value(path)

    def test_posix_permission_and_owner_checks_fail_closed(self):
        with (
            patch("audiodigest.private_store._POSIX", True),
            patch("audiodigest.private_store.os.getuid", return_value=1000, create=True),
        ):
            for mode, uid in ((0o644, 1000), (0o600, 1001)):
                info = SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=uid, st_nlink=1)
                with self.subTest(mode=mode, uid=uid), self.assertRaises(PrivateStoreError):
                    _assert_owner_only_file(info)
            _assert_owner_only_file(SimpleNamespace(
                st_mode=stat.S_IFREG | 0o600, st_uid=1000, st_nlink=1,
            ))

    def test_hard_link_alias_is_refused_without_modifying_either_file(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            target, alias = root / "target", root / "alias"
            write_private_value(target, "original")
            try:
                os.link(target, alias)
            except OSError:
                self.skipTest("hard links are unavailable")
            for action in (lambda: read_private_value(alias),
                           lambda: write_private_value(alias, "replacement"),
                           lambda: delete_private_value(alias)):
                with self.assertRaises(PrivateStoreError):
                    action()
            self.assertEqual("original", target.read_text())
            self.assertTrue(alias.exists())

    def test_linked_ancestor_is_refused_before_secret_is_written(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            target, link = root / "target", root / "link"
            target.mkdir()
            try:
                link.symlink_to(target, target_is_directory=True)
            except OSError:
                self.skipTest("symbolic links are unavailable")
            with self.assertRaises(PrivateStoreError):
                write_private_value(link / "nested" / "secret", "never written")
            self.assertEqual([], list(target.iterdir()))

    def test_failed_atomic_replace_preserves_previous_value_and_removes_temporary(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "secret"
            write_private_value(path, "original")
            with patch("audiodigest.private_store.os.replace", side_effect=OSError("failure")):
                with self.assertRaises(PrivateStoreError):
                    write_private_value(path, "replacement")
            self.assertEqual("original", read_private_value(path))
            self.assertEqual([], list(path.parent.glob(".*.tmp")))

    def test_failed_posix_directory_permissions_prevent_secret_materialization(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "nested" / "secret"
            with (
                patch("audiodigest.private_store._POSIX", True),
                patch("audiodigest.private_store.Path.chmod", side_effect=OSError("failure")),
            ):
                with self.assertRaises(PrivateStoreError):
                    write_private_value(path, "never written")
            self.assertFalse(path.exists())

    def test_generated_posix_modes_are_owner_only(self):
        if os.name != "posix":
            self.skipTest("POSIX modes are checked on Linux CI")
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "nested" / "secret"
            write_private_value(path, "private-value")
            self.assertEqual(0o700, stat.S_IMODE(path.parent.stat().st_mode))
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))

    def test_new_limit_is_bounded_and_default_credentials_stay_two_mib(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "secret"
            for limit in (True, 0, 17 * 1024 * 1024):
                with self.subTest(limit=limit), self.assertRaises(PrivateStoreError):
                    write_private_value(path, "value", maximum_bytes=limit)
            with self.assertRaises(PrivateStoreError):
                write_private_value(path, "x" * (2 * 1024 * 1024 + 1))
            self.assertFalse(path.exists())

    def test_private_file_round_trip(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "secret"
            write_private_value(path, "private-value")
            self.assertEqual("private-value", read_private_value(path))
            self.assertTrue(delete_private_value(path))
            self.assertIsNone(read_private_value(path))

    def test_symbolic_link_is_refused(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            target = root / "target"
            target.write_text("secret", encoding="utf-8")
            link = root / "link"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symbolic links are unavailable")
            with self.assertRaises(PrivateStoreError):
                read_private_value(link)

    def test_gmail_and_runner_can_use_ephemeral_files(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            settings = load_settings("config.example.toml")
            settings.gmail.token_file_path = root / "gmail-token"
            settings.web.token_file_path = root / "runner-token"
            gmail = GmailTokenStore(settings)
            runner = WebRunnerTokenStore(settings)
            gmail.set('{"refresh_token":"test"}')
            runner.set("firebase-refresh")
            self.assertIn("refresh_token", gmail.get())
            self.assertEqual("firebase-refresh", runner.get())
