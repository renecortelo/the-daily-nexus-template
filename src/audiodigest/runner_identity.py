"""Explicit operator migration; never called by generation or a scheduled job."""

from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from audiodigest.config import Settings
from audiodigest.web_runner import (
    FirebaseWebRunnerClient,
    WebRunnerError,
    WebRunnerTokenStore,
    _authenticate_owner,
    _encode_firestore_fields,
    _firebase_auth_url,
    _json_request,
)


def migrate_runner_identity(
    settings: Settings,
    install_secret: Callable[[str], None],
    confirm_idle: Callable[[], None],
) -> None:
    """Prepare -> verify -> install private secret -> activate server expiry.

    Prepared grants deliberately retain legacy access until the encrypted cloud
    secret is updated. Failure leaves a resumable state, never restores legacy
    access after activation, and never prints a token or deletes a Firebase user.
    """
    confirm_idle()
    owner = _authenticate_owner(settings)
    owner_token = owner.get('idToken')
    if not isinstance(owner_token, str) or not owner_token:
        raise WebRunnerError('owner identity token is unavailable')
    client = FirebaseWebRunnerClient(settings)
    grant = client._automation_authorization(owner_token)
    if grant and grant.get('mode') != 'prepared':
        raise WebRunnerError('migration already activated or revoked; no automatic rotation')
    store = WebRunnerTokenStore(settings)
    if grant:
        # Resume only the exact locally secured prepared identity.
        client.authenticate()
        token = client._id_token
        if client.identity_uid != grant.get('runnerUid'):
            raise WebRunnerError('prepared identity is missing; do not overwrite its grant')
        refresh = store.get()
    else:
        created = _json_request(
            _firebase_auth_url(settings, 'accounts:signUp'),
            payload={'returnSecureToken': True},
        )
        runner_uid = created.get('localId')
        refresh = created.get('refreshToken')
        token = created.get('idToken')
        if not (
            isinstance(runner_uid, str) and runner_uid and runner_uid != client.uid
            and len(runner_uid) <= 128 and '/' not in runner_uid
            and isinstance(refresh, str) and refresh
            and isinstance(token, str) and token
        ):
            raise WebRunnerError('Firebase did not create a complete dedicated identity')
        grant = {'runnerUid': runner_uid, 'mode': 'prepared', 'schemaVersion': 1}
        # Store before authorizing: a vault error cannot leave an unowned grant.
        store.set(refresh)
        _write_grant(client, owner_token, grant)
    if not isinstance(refresh, str) or not refresh:
        raise WebRunnerError('dedicated runner refresh token is unavailable')
    # A real, bounded read exercises the deployed rules without claiming a task.
    client._id_token = token
    client.list_private_collection('schedules', limit=1)
    install_secret(refresh)
    confirm_idle()
    _write_grant(client, owner_token, {**grant, 'mode': 'active'})


def _write_grant(
    client: FirebaseWebRunnerClient, token: str, grant: dict[str, Any],
) -> None:
    uid = quote(client.uid, safe='')
    _json_request(
        f'{client._documents_root}/automationAuthorizations/{uid}',
        method='PATCH', payload={'fields': _encode_firestore_fields(grant)}, bearer=token,
    )
