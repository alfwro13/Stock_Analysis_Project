import contextlib

from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import Response

from .server import MCP_PATH, server


class _Endpoint:
    """ASGI endpoint for /mcp; answers 405 to everything but POST because stateless JSON mode has no stream to open (the SDK would hold a GET open forever)."""

    __name__ = "mcp_endpoint"  # SlowAPIMiddleware reads handler.__name__

    def __init__(self):
        self.active = []

    async def __call__(self, scope, receive, send):
        if scope["method"] != "POST":
            response = Response(status_code=405, headers={"Allow": "POST"})
        elif not self.active:
            response = Response(status_code=503)
        else:
            await self.active[-1](scope, receive, send)
            return
        await response(scope, receive, send)


endpoint = _Endpoint()


def _transport_security(hosts) -> TransportSecuritySettings:
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[*hosts, *(f"{host}:*" for host in hosts)],
        allowed_origins=[f"{scheme}://{host}{port}" for host in hosts for scheme in ("http", "https") for port in ("", ":*")],
    )


@contextlib.asynccontextmanager
async def run(trusted_hosts):
    # The SDK's session manager can only run once, so every app lifespan (several per process under test) builds its own.
    app = server.streamable_http_app(
        streamable_http_path=MCP_PATH, stateless_http=True, json_response=True,
        transport_security=_transport_security(trusted_hosts),
    )
    manager = server.session_manager
    endpoint.active.append(app)
    try:
        async with manager.run():
            yield
    finally:
        endpoint.active.remove(app)
