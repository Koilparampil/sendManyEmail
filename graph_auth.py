"""App-only Microsoft Graph tokens for an Entra ID app registration."""

from __future__ import annotations

import threading

import msal

GRAPH_SCOPES = ["https://graph.microsoft.com/.default"]


class GraphAuthError(Exception):
    """Couldn't get a Microsoft Graph token. The message is shown to the user."""


class GraphTokenProvider:
    """Gets tokens with the client-credentials flow.

    Keep one provider per app registration for the life of the app: MSAL
    caches the token and gets a new one shortly before it expires, so calling
    token() before every request is cheap and never returns an expired token.
    """

    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self._client_secret = client_secret
        self._app: msal.ConfidentialClientApplication | None = None
        self._lock = threading.Lock()

    def token(self) -> str:
        """Return a valid access token. Raises GraphAuthError."""
        with self._lock:
            try:
                if self._app is None:  # created lazily: MSAL contacts Entra ID when it starts
                    self._app = msal.ConfidentialClientApplication(
                        self.client_id,
                        authority=f"https://login.microsoftonline.com/{self.tenant_id}",
                        client_credential=self._client_secret,
                    )
                result = self._app.acquire_token_for_client(scopes=GRAPH_SCOPES)
            except Exception as exc:  # network errors, bad tenant ID, ...
                raise GraphAuthError(f"Couldn't reach Microsoft to sign in app {self.client_id}: {exc}") from exc
        if result and "access_token" in result:
            return result["access_token"]
        result = result or {}
        detail = result.get("error_description") or result.get("error") or "unknown error"
        raise GraphAuthError(f"Microsoft wouldn't sign in app {self.client_id}: {detail}")
