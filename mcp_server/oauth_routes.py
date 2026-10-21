import base64
import binascii
import functools
import logging
import time
from typing import Dict, Optional, Tuple
from urllib.parse import parse_qsl, unquote_plus

from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from config import BASE_DIR
from constants import MCP_AUTH_FAILURES_MAX, MCP_AUTH_LOCKOUT_SECONDS, MCP_OAUTH_FORM_MAX_BYTES, MCP_OAUTH_PARAM_MAX_CHARS, MCP_OAUTH_REQUEST_SECONDS

from . import audit, events, limits, oauth
from .auth import disabled_response
from .oauth import OAuthError

logger = logging.getLogger(__name__)

BINDING_COOKIE = "mcp_oauth_binding"
_APPROVE_CSS = BASE_DIR / "static" / "css" / "oauth_approve.css"
_APPROVE_JS = BASE_DIR / "static" / "js" / "oauth_approve.js"
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _precheck(request: Request) -> Optional[Response]:
    if not oauth.on_public_host(request.headers.get("host", "")):
        return Response(status_code=404)
    return disabled_response()


def _finish(request: Request, name: str, started: float, response: Response, grant_id: Optional[int] = None) -> Response:
    audit.record(None, _address(request), "oauth", name, response.status_code, int((time.monotonic() - started) * 1000), len(response.body), grant_id)
    return response


def _error_response(e: OAuthError) -> Response:
    if e.status == 403:
        return PlainTextResponse(e.description, status_code=403, headers=_NO_STORE)
    headers = dict(_NO_STORE)
    if e.retry_after:
        headers["Retry-After"] = str(e.retry_after)
    if e.status == 401:
        headers["WWW-Authenticate"] = 'Basic realm="oauth"'
    return JSONResponse({"error": e.error, "error_description": e.description}, status_code=e.status, headers=headers)


def _lockout_response(request: Request, name: str, started: float) -> Optional[Response]:
    address = _address(request)
    wait = limits.lockout_remaining(address)
    if not wait:
        return None
    response = _error_response(OAuthError("temporarily_unavailable", "Too many failed attempts from this address.", status=429, retry_after=wait))
    if limits.first_refusal(("address", address)):
        _finish(request, name, started, response)
    return response


async def protected_resource_metadata(request: Request) -> Response:
    refused = _precheck(request)
    if refused is not None:
        return refused
    return JSONResponse({
        "resource": oauth.resource_url(),
        "authorization_servers": [oauth.public_origin()],
        "scopes_supported": [oauth.SCOPE],
        "bearer_methods_supported": ["header"],
    })


async def authorization_server_metadata(request: Request) -> Response:
    refused = _precheck(request)
    if refused is not None:
        return refused
    origin = oauth.public_origin()
    return JSONResponse({
        "issuer": origin,
        "authorization_endpoint": f"{origin}/oauth/authorize",
        "token_endpoint": f"{origin}/oauth/token",
        "revocation_endpoint": f"{origin}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
        "revocation_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
        "scopes_supported": [oauth.SCOPE],
        "authorization_response_iss_parameter_supported": True,
    })


def _single_valued(pairs) -> Dict[str, str]:
    fields: Dict[str, str] = {}
    for name, value in pairs:
        if name in fields or len(value) > MCP_OAUTH_PARAM_MAX_CHARS:
            raise OAuthError("invalid_request", "A parameter is repeated or too long.")
        fields[name] = value
    return fields


async def authorize(request: Request) -> Response:
    refused = _precheck(request)
    if refused is not None:
        return refused
    started = time.monotonic()
    try:
        params = _single_valued(request.query_params.multi_items())
        binding = await run_in_threadpool(oauth.begin_authorization, params, _address(request), request.headers.get("user-agent"))
    except OAuthError as e:
        if e.redirect:
            response = RedirectResponse(
                oauth.redirect_url({"error": e.error, "error_description": e.description, "state": e.state}), status_code=302, headers=_NO_STORE
            )
        else:
            response = _error_response(e)
    else:
        response = RedirectResponse("/oauth/approve", status_code=302, headers=_NO_STORE)
        response.set_cookie(BINDING_COOKIE, binding, max_age=MCP_OAUTH_REQUEST_SECONDS, path="/oauth", httponly=True, secure=True, samesite="lax")
    return _finish(request, "authorize", started, response)


@functools.lru_cache(maxsize=1)
def _approve_page() -> str:
    # The public host serves no /static, so the page carries its own CSS and JS, read from the repository files and never from the request.
    return templates.get_template("oauth_approve.html").render(
        css=_APPROVE_CSS.read_text(encoding="utf-8"), js=_APPROVE_JS.read_text(encoding="utf-8"), redirect_prefix=oauth.REDIRECT_URI
    )


async def approve(request: Request) -> Response:
    refused = _precheck(request)
    if refused is not None:
        return refused
    started = time.monotonic()
    page = await run_in_threadpool(_approve_page)
    return _finish(request, "approve", started, HTMLResponse(page, headers=_NO_STORE))


async def approve_status(request: Request) -> Response:
    refused = _precheck(request)
    if refused is not None:
        return refused
    state = await run_in_threadpool(oauth.request_state, request.cookies.get(BINDING_COOKIE, ""))
    return JSONResponse(state, headers=_NO_STORE)


async def _read_form(request: Request) -> Dict[str, str]:
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/x-www-form-urlencoded":
        raise OAuthError("invalid_request", "The body must be application/x-www-form-urlencoded.")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MCP_OAUTH_FORM_MAX_BYTES:
            raise OAuthError("invalid_request", "The request body is too large.", status=413)
    try:
        pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True, strict_parsing=True, max_num_fields=30)
    except ValueError:
        raise OAuthError("invalid_request", "The body is not valid form data.") from None
    return _single_valued(pairs)


def _client_credentials(request: Request, fields: Dict[str, str]) -> Tuple[str, str]:
    header = request.headers.get("authorization", "")
    posted = fields.get("client_secret")
    if header[:6].lower() != "basic ":
        return fields.get("client_id", ""), posted or ""
    if posted is not None:
        raise OAuthError("invalid_request", "Use one client authentication method.")
    try:
        decoded = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        return "", ""
    client_id, separator, secret = decoded.partition(":")
    client_id = unquote_plus(client_id)
    if not separator or fields.get("client_id", client_id) != client_id:
        return "", ""
    return client_id, unquote_plus(secret)


async def _authenticate(request: Request, fields: Dict[str, str]) -> str:
    client_id, secret = _client_credentials(request, fields)
    if await run_in_threadpool(oauth.authenticate_client, client_id, secret):
        return client_id
    address = _address(request)
    if limits.record_failure(address):
        logger.warning("Locked %s out of the MCP endpoints for %d s after %d wrong credentials", address, MCP_AUTH_LOCKOUT_SECONDS, MCP_AUTH_FAILURES_MAX)
        events.lockout_tripped(address)
    raise OAuthError("invalid_client", "Client authentication failed.", status=401)


def _grant(client_id: str, fields: Dict[str, str]) -> Tuple[dict, int]:
    grant_type = fields.get("grant_type")
    if grant_type == "authorization_code":
        if not all(fields.get(name) for name in ("code", "redirect_uri", "code_verifier")):
            raise OAuthError("invalid_request", "code, redirect_uri and code_verifier are required.")
        return oauth.exchange_code(fields["code"], fields["redirect_uri"], fields["code_verifier"], fields.get("resource"))
    if grant_type == "refresh_token":
        if not fields.get("refresh_token"):
            raise OAuthError("invalid_request", "refresh_token is required.")
        return oauth.refresh_tokens(client_id, fields["refresh_token"], fields.get("scope"), fields.get("resource"))
    raise OAuthError("unsupported_grant_type", "Only authorization_code and refresh_token are supported.")


async def token(request: Request) -> Response:
    refused = _precheck(request)
    if refused is not None:
        return refused
    started = time.monotonic()
    locked = _lockout_response(request, "token", started)
    if locked is not None:
        return locked
    grant_id = None
    try:
        fields = await _read_form(request)
        client_id = await _authenticate(request, fields)
        body, grant_id = await run_in_threadpool(_grant, client_id, fields)
        response = JSONResponse(body, headers=_NO_STORE)
    except OAuthError as e:
        response = _error_response(e)
    return _finish(request, "token", started, response, grant_id)


async def revoke(request: Request) -> Response:
    refused = _precheck(request)
    if refused is not None:
        return refused
    started = time.monotonic()
    locked = _lockout_response(request, "revoke", started)
    if locked is not None:
        return locked
    try:
        fields = await _read_form(request)
        client_id = await _authenticate(request, fields)
        if not fields.get("token"):
            raise OAuthError("invalid_request", "token is required.")
        await run_in_threadpool(oauth.revoke_token, client_id, fields["token"])
        response = Response(status_code=200, headers=_NO_STORE)
    except OAuthError as e:
        response = _error_response(e)
    return _finish(request, "revoke", started, response)


ROUTES = (
    ("/.well-known/oauth-protected-resource/mcp", protected_resource_metadata, ["GET"]),
    ("/.well-known/oauth-authorization-server", authorization_server_metadata, ["GET"]),
    ("/oauth/authorize", authorize, ["GET"]),
    ("/oauth/approve", approve, ["GET"]),
    ("/oauth/approve-status", approve_status, ["GET"]),
    ("/oauth/token", token, ["POST"]),
    ("/oauth/revoke", revoke, ["POST"]),
)
PATHS = frozenset(path for path, _, _ in ROUTES)
