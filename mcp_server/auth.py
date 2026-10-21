import logging
from typing import Optional

from starlette.requests import Request
from starlette.responses import JSONResponse

from config import load_config
from constants import MCP_AUTH_FAILURES_MAX, MCP_AUTH_LOCKOUT_SECONDS

from . import audit, events, keys, limits, oauth
from .server import MCP_PATH

logger = logging.getLogger(__name__)


def _rejection(address: str, status: int, detail: str, headers: Optional[dict] = None, log: bool = True) -> JSONResponse:
    response = JSONResponse({"detail": detail}, status_code=status, headers=headers)
    if log:
        audit.record(None, address, None, None, status, 0, len(response.body))
    return response


def _wrong_credential(address: str, status: int, detail: str, headers: Optional[dict] = None) -> JSONResponse:
    if limits.record_failure(address):
        logger.warning(
            "Locked %s out of %s for %d s after %d wrong credentials", address, MCP_PATH, MCP_AUTH_LOCKOUT_SECONDS, MCP_AUTH_FAILURES_MAX
        )
        events.lockout_tripped(address)
    return _rejection(address, status, detail, headers)


def disabled_response() -> Optional[JSONResponse]:
    if load_config().get("MCP_SERVER_ENABLED"):
        return None
    return JSONResponse({"detail": "The MCP Server is disabled"}, status_code=503)


def _public_host_failure(request: Request, address: str) -> Optional[JSONResponse]:
    authorization = request.headers.get("Authorization", "").strip()
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() == "bearer" and not request.headers.get("X-API-Key"):
        grant_id = oauth.verify_access_token(token.strip())
        if grant_id is not None:
            oauth.record_use(grant_id, address)
            request.state.mcp_grant_id = grant_id
            request.state.mcp_address = address
            return None
    challenge = f'Bearer resource_metadata="{oauth.public_origin()}/.well-known/oauth-protected-resource/mcp", scope="{oauth.SCOPE}"'
    headers = {"WWW-Authenticate": challenge}
    if not authorization and not request.headers.get("X-API-Key"):
        return _rejection(address, 401, "Invalid or missing access token", headers)
    return _wrong_credential(address, 401, "Invalid or missing access token", headers)


def auth_failure(request: Request) -> Optional[JSONResponse]:
    disabled = disabled_response()
    if disabled is not None:
        return disabled
    address = request.client.host if request.client else "unknown"
    wait = limits.lockout_remaining(address)
    if wait:
        return _rejection(
            address, 429, "Too many wrong MCP credentials from this address", {"Retry-After": str(wait)},
            log=limits.first_refusal(("address", address)),
        )
    if oauth.on_public_host(request.headers.get("host", "")):
        return _public_host_failure(request, address)
    if request.headers.get("X-API-Key"):
        return _wrong_credential(address, 403, "The API key cannot be used on this route")
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        key_id = keys.match(token.strip())
        if key_id is not None:
            keys.record_use(key_id)
            request.state.mcp_key_id = key_id
            request.state.mcp_address = address
            return None
    if not keys.has_keys():
        logger.warning("Refused a %s request: no MCP key exists", MCP_PATH)
    return _wrong_credential(address, 401, "Invalid or missing MCP key", {"WWW-Authenticate": "Bearer"})
