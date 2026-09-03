from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

from audiodigest.config import load_settings
from audiodigest.web_runner import (
    WEB_RUNNER_IDENTITY_SCOPES,
    FirebaseWebRunnerClient,
    WebRunnerError,
    _decode_firestore_fields,
    _encode_firestore_fields,
    _json_request,
    authenticate_web_runner,
)


class WebRunnerTests(TestCase):
    def test_pairing_uses_google_canonical_identity_scopes(self):
        self.assertEqual(
            (
                "openid",
                "https://www.googleapis.com/auth/userinfo.email",
            ),
            WEB_RUNNER_IDENTITY_SCOPES,
        )

    def test_pairing_exchanges_short_lived_access_token_not_foreign_id_token(self):
        settings = load_settings(Path("config.example.toml"))
        settings.web.enabled = True
        settings.web.firebase_api_key = "AIza" + ("x" * 35)
        settings.web.owner_uid = "owner-uid"
        settings.web.oauth_client_secret_path = Path("config.example.toml").resolve()
        settings.firebase.base_url = "https://example-private-project.web.app"
        flow = MagicMock()
        flow.run_local_server.return_value = SimpleNamespace(
            token="short-lived-google-access-token",  # noqa: S106 - inert test value
            id_token="foreign-project-id-token",  # noqa: S106 - inert test value
        )
        firebase_response = {
            "localId": "owner-uid",
            "email": "owner@example.com",
            "refreshToken": "firebase-refresh-token",
        }

        with (
            patch(
                "google_auth_oauthlib.flow.InstalledAppFlow.from_client_secrets_file",
                return_value=flow,
            ),
            patch(
                "audiodigest.web_runner._json_request",
                return_value=firebase_response,
            ) as request,
            patch(
                "audiodigest.web_runner.GmailTokenStore.get_account_email",
                return_value="owner@example.com",
            ),
            patch("audiodigest.web_runner.WebRunnerTokenStore.set") as save_token,
        ):
            identity = authenticate_web_runner(settings)

        post_body = request.call_args.kwargs["payload"]["postBody"]
        self.assertIn("access_token=short-lived-google-access-token", post_body)
        self.assertNotIn("id_token=", post_body)
        self.assertEqual("owner-uid", identity.uid)
        save_token.assert_called_once_with("firebase-refresh-token")

    def test_pairing_requires_a_dedicated_local_oauth_client(self):
        settings = load_settings(Path("config.example.toml"))
        settings.web.enabled = True
        settings.web.oauth_client_secret_path = Path("missing-runner-client.json")
        with self.assertRaisesRegex(WebRunnerError, "dedicated Firebase-project"):
            authenticate_web_runner(settings)

    def test_firestore_values_round_trip_without_credentials(self):
        payload = {
            "enabled": True,
            "count": 2,
            "name": "Morning",
            "days": [0, 1, 2],
            "parameters": {"label": "AudioDigest/Source"},
        }
        self.assertEqual(
            payload,
            _decode_firestore_fields(_encode_firestore_fields(payload)),
        )

    def test_remote_helper_refuses_unexpected_hosts(self):
        with self.assertRaises(WebRunnerError):
            _json_request("https://attacker.example/private")

    def test_client_refuses_unapproved_collections_before_network(self):
        settings = load_settings(Path("config.example.toml"))
        settings.web.enabled = True
        settings.web.firebase_api_key = "AIza" + ("x" * 35)
        settings.web.owner_uid = "owner-uid"
        settings.firebase.project_id = "example-private-project"
        settings.firebase.base_url = "https://example-private-project.web.app"
        client = FirebaseWebRunnerClient(settings)
        with self.assertRaises(WebRunnerError):
            client.list_private_collection("owners")
        with self.assertRaises(WebRunnerError):
            client.set_private_document("owners", "owner-uid", {})

    def test_authentication_requires_credential_manager_pairing(self):
        settings = load_settings(Path("config.example.toml"))
        settings.web.enabled = True
        settings.web.firebase_api_key = "AIza" + ("x" * 35)
        settings.web.owner_uid = "owner-uid"
        settings.firebase.project_id = "example-private-project"
        settings.firebase.base_url = "https://example-private-project.web.app"
        client = FirebaseWebRunnerClient(settings)
        with patch.object(client.token_store, "get", return_value=None):
            with self.assertRaisesRegex(WebRunnerError, "not paired"):
                client.authenticate()

    def test_execution_claim_uses_create_only_firestore_write(self):
        settings = load_settings(Path("config.example.toml"))
        settings.web.enabled = True
        settings.web.firebase_api_key = "AIza" + ("x" * 35)
        settings.web.owner_uid = "owner-uid"
        settings.firebase.project_id = "example-private-project"
        settings.firebase.base_url = "https://example-private-project.web.app"
        client = FirebaseWebRunnerClient(settings)
        client._id_token = "short-test-token"  # noqa: S105
        with patch(
            "audiodigest.web_runner._json_request",
            return_value={"_conflict": True},
        ) as request:
            claimed = client.claim_private_execution(
                "weekday-morning",
                "2026-07-27",
            )
        self.assertFalse(claimed)
        self.assertEqual("POST", request.call_args_list[0].kwargs["method"])
        self.assertTrue(request.call_args_list[0].kwargs["allow_conflict"])

    def test_failed_execution_claim_remains_terminal(self):
        settings = load_settings(Path("config.example.toml"))
        settings.web.enabled = True
        settings.web.firebase_api_key = "AIza" + ("x" * 35)
        settings.web.owner_uid = "owner-uid"
        settings.firebase.project_id = "example-private-project"
        settings.firebase.base_url = "https://example-private-project.web.app"
        client = FirebaseWebRunnerClient(settings)
        client._id_token = "short-test-token"  # noqa: S105
        with patch(
            "audiodigest.web_runner._json_request",
            side_effect=[
                {"_conflict": True},
            ],
        ) as request:
            claimed = client.claim_private_execution(
                "weekday-morning",
                "2026-07-27",
            )
        self.assertFalse(claimed)
        self.assertEqual("POST", request.call_args_list[0].kwargs["method"])
        self.assertEqual(1, request.call_count)

    def test_firestore_rules_keep_failed_executions_terminal(self):
        rules = Path("firestore.rules").read_text(encoding="utf-8")
        self.assertNotIn("resource.data.status == 'failed'", rules)
        self.assertIn("request.resource.data.status == 'running'", rules)

    def test_list_private_collection_with_field_mask_appends_query_parameters(self):
        settings = load_settings(Path("config.example.toml"))
        settings.web.enabled = True
        settings.web.firebase_api_key = "AIza" + ("x" * 35)
        settings.web.owner_uid = "owner-uid"
        settings.firebase.project_id = "example-private-project"
        settings.firebase.base_url = "https://example-private-project.web.app"
        client = FirebaseWebRunnerClient(settings)
        client._id_token = "short-test-token"  # noqa: S105
        with patch(
            "audiodigest.web_runner._json_request",
            return_value={"documents": []},
        ) as request:
            items = client.list_private_collection(
                "episodes",
                field_mask=["episodeDate", "publicationSequence"],
            )
        self.assertEqual([], items)
        url = request.call_args_list[0].args[0]
        self.assertIn("mask.fieldPaths=episodeDate", url)
        self.assertIn("mask.fieldPaths=publicationSequence", url)
        self.assertIn("pageSize=100", url)
