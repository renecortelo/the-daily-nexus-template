import json
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from audiodigest.gmail_client import GmailClient, GmailTokenStore


class GmailTokenStoreTests(TestCase):
    def test_delete_removes_token_from_keyring(self):
        keyring = SimpleNamespace(delete_password=Mock())
        errors = SimpleNamespace(PasswordDeleteError=RuntimeError)
        store = GmailTokenStore.__new__(GmailTokenStore)
        store.service = "AudioDigest"
        store.username = "gmail-oauth-token"

        with patch.dict(
            "sys.modules",
            {"keyring": keyring, "keyring.errors": errors},
        ):
            self.assertTrue(store.delete())

        keyring.delete_password.assert_called_once_with("AudioDigest", "gmail-oauth-token")


class GmailLogoutTests(TestCase):
    def test_account_email_is_read_from_profile_and_cached(self):
        client = GmailClient.__new__(GmailClient)
        client.token_store = Mock()
        client._service = Mock()
        client._service.users.return_value.getProfile.return_value.execute.return_value = {
            "emailAddress": "reader@example.com"
        }

        self.assertEqual("reader@example.com", client.account_email())

        client._service.users.return_value.getProfile.assert_called_once_with(userId="me")
        client.token_store.set_account_email.assert_called_once_with("reader@example.com")

    def test_candidate_label_is_checked_against_connected_mailbox(self):
        client = GmailClient.__new__(GmailClient)
        client.settings = Mock()
        client.token_store = Mock()
        client._service = Mock()
        label_response = client._service.users.return_value.labels.return_value.list.return_value
        label_response.execute.return_value = {
            "labels": [{"id": "Label_123", "name": "Briefings/AI"}]
        }

        self.assertEqual("Label_123", client.verify_label("Briefings/AI"))

    def test_logout_revokes_refresh_token_then_deletes_local_token(self):
        client = GmailClient.__new__(GmailClient)
        client.token_store = Mock()
        client.token_store.get.return_value = json.dumps(
            {"refresh_token": "private-refresh-token", "token": "access-token"}
        )
        client.token_store.delete.return_value = True
        client._service = object()
        response = Mock()
        response.status = 200
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)

        with patch(
            "audiodigest.gmail_client.urllib.request.urlopen",
            return_value=response,
        ) as urlopen:
            result = client.logout()

        self.assertTrue(result.had_authorization)
        self.assertTrue(result.remote_revoked)
        self.assertTrue(result.local_deleted)
        request = urlopen.call_args.args[0]
        self.assertIn(b"private-refresh-token", request.data)
        client.token_store.delete.assert_called_once_with()
        client.token_store.delete_account_email.assert_called_once_with()
        self.assertIsNone(client._service)

    def test_logout_without_saved_token_is_idempotent(self):
        client = GmailClient.__new__(GmailClient)
        client.token_store = Mock()
        client.token_store.get.return_value = None
        client._service = None

        result = client.logout()

        self.assertFalse(result.had_authorization)
        self.assertFalse(result.local_deleted)
        client.token_store.delete.assert_not_called()
