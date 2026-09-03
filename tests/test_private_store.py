import tempfile
from pathlib import Path
from unittest import TestCase

from audiodigest.config import load_settings
from audiodigest.gmail_client import GmailTokenStore
from audiodigest.private_store import (
    PrivateStoreError,
    delete_private_value,
    read_private_value,
    write_private_value,
)
from audiodigest.web_runner import WebRunnerTokenStore


class PrivateStoreTests(TestCase):
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
