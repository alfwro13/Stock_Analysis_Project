import ipaddress
import logging
import os
from typing import Optional

from starlette.datastructures import Headers
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from config import load_config
from utils import clean_display_text

from . import audit, events, limits
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
_PROXY_LIKE_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "100.64.0.0/10", "::1/128", "fc00::/7", "fe80::/10",
))
_FORWARDED_VALUE_MAX_CHARS = 200


def normalize_host(header: str) -> str:
    # Same extraction as TrustedHostMiddleware, plus case and a trailing dot, so a wildcard ALLOWED_HOSTS entry cannot let a variant spelling past the guard.
    return header.split(":")[0].lower().rstrip(".")


def _parse_address(address: str):
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip


def is_proxy_like(address: str) -> bool:
    """A loopback, private or link-local peer: on the public host that is the reverse proxy itself, never a visitor."""
    ip = _parse_address(address)
    return ip is not None and any(ip in network for network in _PROXY_LIKE_NETWORKS)


def forwarded_trust() -> dict:
    """What uvicorn was told to trust (FORWARDED_ALLOW_IPS): unset, loopback, wildcard or list."""
    value = os.environ.get("FORWARDED_ALLOW_IPS", "").strip()
    entries = [entry.strip() for entry in value.split(",") if entry.strip()]
    if not entries:
        state = "unset"
    elif "*" in entries:
        state = "wildcard"
    elif all(_is_loopback_entry(entry) for entry in entries):
        state = "loopback"
    else:
        state = "list"
    return {"state": state, "value": clean_display_text(value, _FORWARDED_VALUE_MAX_CHARS) or ""}


def _is_loopback_entry(entry: str) -> bool:
    try:
        return ipaddress.ip_network(entry, strict=False).is_loopback
    except ValueError:
        return False


def warn_if_proxy_untrusted(public_host: str) -> None:
    if not public_host:
        return
    state = forwarded_trust()["state"]
    if state == "wildcard":
        logger.warning("FORWARDED_ALLOW_IPS is '*' while MCP_PUBLIC_HOST is set: any host that can reach the app can forge the client address")
    elif state == "unset":
        logger.warning(
            "FORWARDED_ALLOW_IPS is not set while MCP_PUBLIC_HOST is set: only a proxy on this machine is trusted, "
            "so behind a proxy on another machine every caller shares the proxy's address"
        )


def address_allowed(address: str, cidrs) -> bool:
    ip = _parse_address(address)
    if ip is None:
        return False
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
        if normalize_host(Headers(scope=scope).get("host", "")) != self.public_host:
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
        client = scope.get("client")
        address = client[0] if client else "unknown"
        limits.note_public_peer(address)
        if route in MACHINE_ROUTES:
            if not address_allowed(address, config.get("MCP_REMOTE_ALLOWED_CIDRS")):
                if limits.first_refusal(("range", address)):
                    proxy_like = is_proxy_like(address)
                    if proxy_like:
                        logger.warning(
                            "Refused %s from %s: a private address on the public host, so the proxy is probably not trusted in FORWARDED_ALLOW_IPS",
                            scope["path"], address,
                        )
                    else:
                        logger.warning("Refused %s from %s: outside MCP_REMOTE_ALLOWED_CIDRS", scope["path"], address)
                    audit.record(None, address, None, None, 403, 0, 0)
                    events.off_range_request(address, proxy_like)
                return Response(status_code=403)
        return None
