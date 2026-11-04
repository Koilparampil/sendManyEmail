import msal


def get_access_token(client_id: str, tenant_id: str, client_secret: str) -> str:
    authority = f"https://login.microsoftonline.com/{tenant_id}"
    app = msal.ConfidentialClientApplication(
        client_id, authority=authority, client_credential=client_secret
    )
    result = app.acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" in result:
        return result["access_token"]
    error = result.get("error_description", result.get("error", "Unknown error"))
    raise RuntimeError(f"Failed to acquire token: {error}")
