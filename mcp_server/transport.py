import contextlib
import time
from collections import deque

from mcp.server.transport_security import TransportSecuritySettings
from starlette.responses import JSONResponse, Response

from constants import MCP_REQUEST_MAX_BYTES

from . import audit, limits
from .server import MCP_PATH, server


class _BodyTooLarge(Exception):
    pass


async def _buffered(receive):
    """Reads the request body (capped) and returns it with a receive() that replays it to the SDK."""
    messages, size = [], 0
    while True:
        message = await receive()
        messages.append(message)
        if message["type"] != "http.request":
            break
        size += len(message.get("body", b""))
        if size > MCP_REQUEST_MAX_BYTES:
            raise _BodyTooLarge
        if not message.get("more_body"):
            break
    body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.request")
    queued = deque(messages)

    async def replay():
        return queued.popleft() if queued else await receive()

    return body, replay


class _Endpoint:
    """ASGI endpoint for /mcp; answers 405 to everything but POST because stateless JSON mode has no stream to open (the SDK would hold a GET open forever)."""

    __name__ = "mcp_endpoint"  # SlowAPIMiddleware reads handler.__name__

    def __init__(self):
        self.active = []

    async def _respond(self, scope, receive, send):
        if scope["method"] != "POST":
            response = Response(status_code=405, headers={"Allow": "POST"})
        elif not self.active:
            response = Response(status_code=503)
        else:
            await self.active[-1](scope, receive, send)
            return
        await response(scope, receive, send)

    async def __call__(self, scope, receive, send):
        started = time.monotonic()
        state = scope.get("state", {})
        key_id, address = state.get("mcp_key_id"), state.get("mcp_address", "unknown")
        outcome = {"status": 500, "bytes": 0}
        method = name = None

        async def traced_send(message):
            if message["type"] == "http.response.start":
                outcome["status"] = message["status"]
            elif message["type"] == "http.response.body":
                outcome["bytes"] += len(message.get("body", b""))
            await send(message)

        def log_call():
            audit.record(key_id, address, method, name, outcome["status"], int((time.monotonic() - started) * 1000), outcome["bytes"])

        retry_after = limits.acquire(key_id)
        if retry_after:
            await JSONResponse(
                {"detail": "Too many MCP requests for this key"}, status_code=429, headers={"Retry-After": str(retry_after)}
            )(scope, receive, traced_send)
            if limits.first_refusal(("key", key_id)):
                log_call()
            return
        try:
            if scope["method"] == "POST" and self.active:
                try:
                    body, receive = await _buffered(receive)
                except _BodyTooLarge:
                    await JSONResponse({"detail": "The request body is too large"}, status_code=413)(scope, receive, traced_send)
                    return
                method, name = audit.describe_request(body)
            await self._respond(scope, receive, traced_send)
        finally:
            limits.release(key_id)
            log_call()


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
