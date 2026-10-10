from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

from audiodigest.config import load_settings
from audiodigest.runner_identity import migrate_runner_identity
from audiodigest.web_runner import FirebaseWebRunnerClient, WebRunnerError, _encode_firestore_fields


class RunnerIdentityTests(TestCase):
    def setUp(self):
        self.settings = load_settings(Path('config.example.toml'))
        self.settings.web.enabled = True
        self.settings.web.owner_uid = 'synthetic-owner'
        self.settings.firebase.project_id = 'example-private-project'
        self.grant = {'runnerUid': 'synthetic-runner', 'mode': 'active', 'schemaVersion': 1}

    def test_separate_identity_keeps_owner_paths_and_rotates_only_after_grant_check(self):
        client = FirebaseWebRunnerClient(self.settings)
        with (
            patch.object(client.token_store, 'get', return_value='synthetic-refresh'),
            patch.object(client.token_store, 'set') as store,
            patch('audiodigest.web_runner._json_request', side_effect=[
                {'user_id': 'synthetic-runner', 'id_token': 'synthetic-id',
                 'refresh_token': 'rotated-synthetic-refresh'},
                {'fields': _encode_firestore_fields(self.grant)},
            ]) as request,
        ):
            self.assertEqual('synthetic-id', client.authenticate())
            self.assertEqual('synthetic-owner', client.uid)
            self.assertEqual('synthetic-runner', client.identity_uid)
            self.assertIn('/automationAuthorizations/synthetic-owner',
                          request.call_args_list[1].args[0])
            store.assert_called_once_with('rotated-synthetic-refresh')

    def test_wrong_revoked_or_malformed_authorization_cannot_cache_or_save_token(self):
        for grant in [None, {**self.grant, 'mode': 'revoked'},
                      {**self.grant, 'runnerUid': 'synthetic-stranger'},
                      {**self.grant, 'schemaVersion': 2}]:
            client = FirebaseWebRunnerClient(self.settings)
            with (
                patch.object(client.token_store, 'get', return_value='synthetic-refresh'),
                patch.object(client.token_store, 'set') as store,
                patch.object(client, '_automation_authorization', return_value=grant),
                patch('audiodigest.web_runner._json_request', return_value={
                    'user_id': 'synthetic-runner', 'id_token': 'synthetic-id',
                    'refresh_token': 'rotated-synthetic-refresh',
                }),
                self.assertRaises(WebRunnerError),
            ):
                client.authenticate()
            self.assertEqual('', client._id_token)
            store.assert_not_called()

    def test_legacy_identity_is_rejected_after_activation_but_works_before(self):
        for grant, accepted in [(None, True), ({**self.grant, 'mode': 'prepared'}, True),
                                (self.grant, False), ({**self.grant, 'mode': 'revoked'}, False)]:
            client = FirebaseWebRunnerClient(self.settings)
            with (
                patch.object(client.token_store, 'get', return_value='synthetic-refresh'),
                patch.object(client, '_automation_authorization', return_value=grant),
                patch('audiodigest.web_runner._json_request', return_value={
                    'user_id': client.uid, 'id_token': 'synthetic-id',
                }),
            ):
                if accepted:
                    client.authenticate()
                else:
                    with self.assertRaises(WebRunnerError):
                        client.authenticate()

    def migration(self, requests=None, grant=None):
        return (
            patch('audiodigest.runner_identity._authenticate_owner',
                  return_value={'idToken': 'synthetic-owner-id'}),
            patch.object(FirebaseWebRunnerClient, '_automation_authorization', return_value=grant),
            patch('audiodigest.runner_identity._json_request', side_effect=requests or [
                {'localId': 'synthetic-runner', 'idToken': 'synthetic-runner-id',
                 'refreshToken': 'synthetic-refresh'}, {}, {},
            ]),
            patch('audiodigest.runner_identity.WebRunnerTokenStore.set'),
            patch.object(FirebaseWebRunnerClient, 'list_private_collection', return_value=[]),
        )

    def test_secret_installed_before_activation_without_generation_or_schedule_write(self):
        installed = []
        mocks = self.migration()
        with mocks[0], mocks[1], mocks[2] as request, mocks[3], mocks[4] as read:
            def install(value):
                self.assertEqual(
                    'prepared', request.call_args.kwargs['payload']['fields']['mode']['stringValue']
                )
                installed.append(value)
            migrate_runner_identity(self.settings, install, lambda: None)
        self.assertEqual(['synthetic-refresh'], installed)
        self.assertEqual(
            'active', request.call_args.kwargs['payload']['fields']['mode']['stringValue']
        )
        read.assert_called_once_with('schedules', limit=1)

    def test_secret_failure_does_not_activate_or_repeat_provisioning(self):
        mocks = self.migration()
        with mocks[0], mocks[1], mocks[2] as request, mocks[3], mocks[4]:
            with self.assertRaisesRegex(RuntimeError, 'synthetic failure'):
                migrate_runner_identity(
                    self.settings, MagicMock(side_effect=RuntimeError('synthetic failure')),
                    lambda: None,
                )
        self.assertEqual(2, request.call_count)
        self.assertEqual(
            'prepared', request.call_args.kwargs['payload']['fields']['mode']['stringValue']
        )

    def test_non_idle_runner_or_activated_grant_prevents_provisioning(self):
        with patch('audiodigest.runner_identity._authenticate_owner') as owner:
            with self.assertRaises(WebRunnerError):
                migrate_runner_identity(self.settings, MagicMock(),
                                        MagicMock(side_effect=WebRunnerError('runner busy')))
            owner.assert_not_called()
        mocks = self.migration(grant=self.grant)
        with mocks[0], mocks[1], mocks[2] as request, mocks[3], mocks[4]:
            with self.assertRaisesRegex(WebRunnerError, 'activated or revoked'):
                migrate_runner_identity(self.settings, MagicMock(), lambda: None)
        request.assert_not_called()

    def test_runner_start_after_install_leaves_prepared_grant_instead_of_breaking_job(self):
        idle = MagicMock(side_effect=[None, WebRunnerError('runner started')])
        install = MagicMock()
        mocks = self.migration()
        with mocks[0], mocks[1], mocks[2] as request, mocks[3], mocks[4]:
            with self.assertRaisesRegex(WebRunnerError, 'runner started'):
                migrate_runner_identity(self.settings, install, idle)
        install.assert_called_once_with('synthetic-refresh')
        self.assertEqual(2, request.call_count)

    def test_resume_prepared_identity_does_not_create_another_account(self):
        prepared = {**self.grant, 'mode': 'prepared'}
        mocks = self.migration(requests=[{}], grant=prepared)
        install = MagicMock()

        def authenticate(client):
            client.identity_uid = 'synthetic-runner'
            client._id_token = 'synthetic-runner-id'  # noqa: S105 - inert test identity.

        with (mocks[0], mocks[1], mocks[2] as request, mocks[3], mocks[4],
              patch.object(FirebaseWebRunnerClient, 'authenticate', authenticate),
              patch('audiodigest.runner_identity.WebRunnerTokenStore.get',
                    return_value='synthetic-refresh')):
            migrate_runner_identity(self.settings, install, lambda: None)
        self.assertEqual(1, request.call_count)
        self.assertIn('/automationAuthorizations/', request.call_args.args[0])
        install.assert_called_once_with('synthetic-refresh')

    def test_resume_refuses_legacy_local_identity_even_with_prepared_grant(self):
        mocks = self.migration(grant={**self.grant, 'mode': 'prepared'})
        install = MagicMock()

        def authenticate(client):
            client.identity_uid = 'synthetic-owner'

        with (mocks[0], mocks[1], mocks[2] as request, mocks[3], mocks[4],
              patch.object(FirebaseWebRunnerClient, 'authenticate', authenticate)):
            with self.assertRaisesRegex(WebRunnerError, 'prepared identity is missing'):
                migrate_runner_identity(self.settings, install, lambda: None)
        request.assert_not_called()
        install.assert_not_called()
