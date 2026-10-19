"""
The CMP as the OAuth authorization server of its own MCP server.

Assistants only ever talk to the CMP. The user signs in through the CMP's
usual Keycloak login in the browser, approves the assistant on a CMP page,
and the CMP issues the assistant its own tokens. Keycloak never sees the
assistant and needs no client per assistant.
"""

from anyio import to_thread
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from app.core.config import settings
from app.services.agents import store


class CmpAuthProvider:
    async def get_client(
        self, client_id: str
    ) -> OAuthClientInformationFull | None:
        info = await to_thread.run_sync(store.load_client, client_id)
        return (
            OAuthClientInformationFull.model_validate(info) if info else None
        )

    async def register_client(
        self, client_info: OAuthClientInformationFull
    ) -> None:
        for uri in client_info.redirect_uris or []:
            if not store.redirect_uri_allowed(str(uri)):
                raise RegistrationError(
                    error="invalid_redirect_uri",
                    error_description=(
                        f"{uri} is not an allowed redirect URI. Ask the "
                        "platform team to allow this assistant."
                    ),
                )
        await to_thread.run_sync(
            store.save_client,
            client_info.client_id,
            client_info.client_name or "Assistant",
            client_info.model_dump(mode="json"),
        )

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if not store.redirect_uri_allowed(str(params.redirect_uri)):
            raise AuthorizeError(
                error="invalid_request",
                error_description="Redirect URI not allowed.",
            )
        request_id = await to_thread.run_sync(
            store.save_auth_request,
            client.client_id,
            params.model_dump(mode="json"),
        )
        return (
            f"{settings.CMP_PUBLIC_URL.rstrip('/')}"
            f"/connect/authorize?request={request_id}"
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = await to_thread.run_sync(
            store.load_auth_code, authorization_code
        )
        if code is None or code.client_id != client.client_id:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=code.scopes,
            expires_at=code.expires_at.timestamp(),
            client_id=code.client_id,
            code_challenge=code.code_challenge,
            redirect_uri=code.redirect_uri,
            redirect_uri_provided_explicitly=code.redirect_uri_provided_explicitly,
            resource=code.resource,
            subject=code.claims.get("sub"),
        )

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        issued = await to_thread.run_sync(
            store.exchange_code, authorization_code.code, client.client_id
        )
        if issued is None:
            raise TokenError(
                error="invalid_grant",
                error_description="Authorization code expired or already used.",
            )
        return _token(issued)

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        grant = await to_thread.run_sync(store.load_refresh, refresh_token)
        if grant is None or grant.client_id != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=grant.client_id,
            scopes=grant.scopes,
            expires_at=int(grant.expires_at.timestamp()),
            subject=grant.subject,
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        issued = await to_thread.run_sync(
            store.rotate_refresh, refresh_token.token, client.client_id, scopes
        )
        if issued is None:
            raise TokenError(
                error="invalid_grant",
                error_description="Refresh token expired or revoked.",
            )
        return _token(issued)

    async def load_access_token(self, token: str) -> AccessToken | None:
        access = await to_thread.run_sync(store.load_access, token)
        if access is None:
            return None
        grant = access.grant
        return AccessToken(
            token=token,
            client_id=grant.client_id,
            scopes=grant.scopes,
            expires_at=int(access.expires_at.timestamp()),
            subject=grant.subject,
            claims={"username": grant.username, "grant_id": grant.id},
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        await to_thread.run_sync(store.revoke_by_token, token.token)


def _token(issued: store.IssuedTokens) -> OAuthToken:
    return OAuthToken(
        access_token=issued.access_token,
        expires_in=issued.expires_in,
        refresh_token=issued.refresh_token,
        scope=" ".join(issued.scopes),
    )
