import ipaddress
import logging
from typing import Optional

from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from config import load_config

from . import audit, limits
from .server import MCP_PATH

logger = logging.getLogger(__name__)

MACHINE_ROUTES = frozenset({
    ("POST", MCP_PATH),
    ("GET", "/.well-known/oauth-protected-resource/mcp"),
    ("GET", "/.well-known/oauth-authorization-server"),
    ("POST", "/oauth/token"),
    ("POST", "/oauth/revoke"),
})
BROWSER_ROUTES = frozenset({
    ("GET", "/oauth/authorize"),
    ("GET", "/oauth/approve"),
    ("GET", "/oauth/approve-status"),
})
PUBLIC_ROUTES = MACHINE_ROUTES | BROWSER_ROUTES


def address_allowed(address: str, cidrs) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    for cidr in cidrs if isinstance(cidrs, list) else ():
        try:
            if ip in ipaddress.ip_network(cidr, strict=False):
                return True
        except ValueError:
            continue
    return False


class PublicHostGuard:
    """Fail-closed allowlist for the public MCP hostname; it reads no cookie, key or auth header, only the Host header, the method, the path and the peer address."""

    def __init__(self, app: ASGIApp, public_host: str):
        self.app = app
        self.public_host = public_host

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not self.public_host or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        # Same extraction as TrustedHostMiddleware, plus case and a trailing dot, so a wildcard ALLOWED_HOSTS entry cannot let a variant spelling past the guard.
        host = Headers(scope=scope).get("host", "").split(":")[0].lower().rstrip(".")
        if host != self.public_host:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        refusal = self._refusal(scope)
        if refusal is None:
            await self.app(scope, receive, send)
        else:
            await refusal(scope, receive, send)

    def _refusal(self, scope: Scope) -> Optional[Response]:
        route = (scope["method"], scope["path"])
        if route not in PUBLIC_ROUTES:
            return Response(status_code=404)
        config = load_config()
        if not config.get("MCP_REMOTE_ENABLED"):
            return Response(status_code=404)
        if route in MACHINE_ROUTES:
            client = scope.get("client")
            address = client[0] if client else "unknown"
            if not address_allowed(address, config.get("MCP_REMOTE_ALLOWED_CIDRS")):
                if limits.first_refusal(("range", address)):
                    logger.warning("Refused %s from %s: outside MCP_REMOTE_ALLOWED_CIDRS", scope["path"], address)
                    audit.record(None, address, None, None, 403, 0, 0)
                return Response(status_code=403)
        return None
