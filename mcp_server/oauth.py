import base64
import hashlib
import hmac
import logging
import re
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlencode

import config
import db_mcp_oauth
from constants import (
    MCP_OAUTH_ACCESS_TOKEN_SECONDS, MCP_OAUTH_CACHE_MAX_ENTRIES, MCP_OAUTH_CODE_SECONDS, MCP_OAUTH_CONNECTED_APPS_MAX, MCP_OAUTH_GRANT_DAYS,
    MCP_OAUTH_LAST_USED_WRITE_SECONDS, MCP_OAUTH_PAIRING_MINUTES, MCP_OAUTH_PARAM_MAX_CHARS, MCP_OAUTH_PENDING_MAX,
    MCP_OAUTH_PRUNE_AFTER_DAYS, MCP_OAUTH_REFRESH_RETRY_GRACE_SECONDS, MCP_OAUTH_REFRESH_TOKEN_DAYS, MCP_OAUTH_REQUEST_SECONDS,
    MCP_OAUTH_REQUESTS_PER_HOUR, MCP_OAUTH_STATE_MAX_CHARS, MCP_OAUTH_USER_AGENT_MAX_CHARS,
)
from time_engine import parse_stored_utc
from utils import clean_display_text

from . import events
from .keys import hash_key
from .public_host import normalize_host
from .server import MCP_PATH

logger = logging.getLogger(__name__)

SCOPE = "mcp:read"
REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"
ACCESS_PREFIX = "mcpat_"
REFRESH_PREFIX = "mcprt_"

_STORED_FORMAT = "%Y-%m-%d %H:%M:%S"
_CHALLENGE_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_BINDING_RE = re.compile(r"[A-Za-z0-9_-]{43}")
_VERIFIER_RE = re.compile(r"[A-Za-z0-9\-._~]{43,128}")
_DISPLAY_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_GRANT_REFUSED = "The grant is invalid, expired or revoked."

_lock = threading.Lock()
_cache: Dict[str, Tuple[int, float]] = {}
_last_written: Dict[int, float] = {}
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-oauth-use")


class OAuthError(Exception):
    def __init__(self, error: str, description: str, status: int = 400, redirect: bool = False, state: Optional[str] = None, retry_after: Optional[int] = None):
        super().__init__(error)
        self.error = error
        self.description = description
        self.status = status
        self.redirect = redirect
        self.state = state
        self.retry_after = retry_after


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.strftime(_STORED_FORMAT)


def _moment(stamp: str) -> datetime:
    parsed = parse_stored_utc(stamp)
    if parsed is None:
        raise ValueError("stored OAuth timestamp is not a UTC timestamp")
    return parsed


def _epoch(stamp: str) -> float:
    return _moment(stamp).timestamp()


def public_origin() -> str:
    return f"https://{config.MCP_PUBLIC_HOST}"


def resource_url() -> str:
    return public_origin() + MCP_PATH


def on_public_host(host_header: str) -> bool:
    return bool(config.MCP_PUBLIC_HOST) and normalize_host(host_header) == config.MCP_PUBLIC_HOST


def redirect_url(params: Dict[str, Optional[str]]) -> str:
    return f"{REDIRECT_URI}?{urlencode({**{k: v for k, v in params.items() if v is not None}, 'iss': public_origin()})}"


def create_client() -> dict:
    client_id = "mcp-" + secrets.token_hex(8)
    secret = secrets.token_hex(32)
    db_mcp_oauth.replace_client(client_id, hash_key(secret), _stamp(_now()))
    invalidate()
    return {"client_id": client_id, "client_secret": secret}


def client_summary() -> Optional[dict]:
    row = db_mcp_oauth.get_client()
    return {"client_id": row["client_id"], "created_at": row["created_at"]} if row else None


def authenticate_client(client_id: str, secret: str) -> bool:
    row = db_mcp_oauth.get_client()
    expected_id, expected_hash = (row["client_id"], row["secret_hash"]) if row else ("", "")
    id_ok = hmac.compare_digest(client_id.encode(), expected_id.encode())
    secret_ok = hmac.compare_digest(hash_key(secret), expected_hash)
    return row is not None and id_ok and secret_ok


def open_pairing_window(minutes: int = MCP_OAUTH_PAIRING_MINUTES) -> Optional[str]:
    """The stored end of the new window, or None when there is no client to pair."""
    until = _stamp(_now() + timedelta(minutes=minutes))
    return until if db_mcp_oauth.set_pairing_open_until(until) else None


def close_pairing_window() -> bool:
    return db_mcp_oauth.set_pairing_open_until(None)


def _window_open(client: dict, now: datetime) -> bool:
    return bool(client["pairing_open_until"]) and client["pairing_open_until"] > _stamp(now)


def pairing_window_open() -> bool:
    client = db_mcp_oauth.get_client()
    return client is not None and _window_open(client, _now())


def pairing_window_until() -> Optional[str]:
    client = db_mcp_oauth.get_client()
    return client["pairing_open_until"] if client is not None and _window_open(client, _now()) else None


def seconds_left(stamp: str) -> int:
    return max(0, int(_epoch(stamp) - _now().timestamp()))


def check_pairing_window(armed_until: str) -> bool:
    """Run when the window armed for armed_until ends; tells the operator when no connection request was made during it."""
    client = db_mcp_oauth.get_client()
    if client is None or client["pairing_open_until"] != armed_until or armed_until > _stamp(_now()):
        return False
    opened = _moment(armed_until) - timedelta(minutes=MCP_OAUTH_PAIRING_MINUTES)
    if db_mcp_oauth.count_requests_since(_stamp(opened)):
        return False
    events.window_closed_unused()
    return True


def begin_authorization(params: Dict[str, str], address: str, user_agent: Optional[str]) -> str:
    """Stores a pending request and returns the secret the requesting browser keeps in its binding cookie."""
    now = _now()
    client = db_mcp_oauth.get_client()
    if client is None or not _window_open(client, now):
        raise OAuthError("access_denied", "Pairing is closed.", status=403)
    if any(len(value) > MCP_OAUTH_PARAM_MAX_CHARS for value in params.values()):
        raise OAuthError("invalid_request", "A parameter is too long.")
    if params.get("client_id") != client["client_id"] or params.get("redirect_uri") != REDIRECT_URI:
        raise OAuthError("invalid_request", "Unknown client or redirect URI.")
    state = params.get("state")
    if not state or len(state) > MCP_OAUTH_STATE_MAX_CHARS:
        raise OAuthError("invalid_request", "A state value is required.", redirect=True)
    if params.get("response_type") != "code":
        raise OAuthError("unsupported_response_type", "Only the code response type is supported.", redirect=True, state=state)
    challenge = params.get("code_challenge", "")
    if params.get("code_challenge_method") != "S256" or not _CHALLENGE_RE.fullmatch(challenge):
        raise OAuthError("invalid_request", "PKCE with the S256 method is required.", redirect=True, state=state)
    scopes = params.get("scope", SCOPE).split()
    if not scopes or set(scopes) - {SCOPE}:
        raise OAuthError("invalid_scope", f"Only the {SCOPE} scope is supported.", redirect=True, state=state)
    if params.get("resource") != resource_url():
        raise OAuthError("invalid_target", "The resource is not this server.", redirect=True, state=state)
    pending, recent = db_mcp_oauth.request_counts(_stamp(now), _stamp(now - timedelta(hours=1)))
    if pending >= MCP_OAUTH_PENDING_MAX or recent >= MCP_OAUTH_REQUESTS_PER_HOUR:
        raise OAuthError("temporarily_unavailable", "Too many connection requests.", status=429, retry_after=MCP_OAUTH_REQUEST_SECONDS)
    binding = secrets.token_urlsafe(32)
    display_code = "".join(secrets.choice(_DISPLAY_CODE_ALPHABET) for _ in range(6))
    db_mcp_oauth.insert_request(
        {
            "id": secrets.token_urlsafe(16),
            "display_code": display_code,
            "client_state": state,
            "code_challenge": challenge,
            "redirect_uri": REDIRECT_URI,
            "resource": resource_url(),
            "scope": SCOPE,
            "created_at": _stamp(now),
            "expires_at": _stamp(now + timedelta(seconds=MCP_OAUTH_REQUEST_SECONDS)),
            "requester_address": clean_display_text(address, 64),
            "user_agent": clean_display_text(user_agent, MCP_OAUTH_USER_AGENT_MAX_CHARS),
            "binding_hash": hash_key(binding),
        },
        _stamp(now - timedelta(days=MCP_OAUTH_PRUNE_AFTER_DAYS)),
    )
    events.request_waiting(display_code, address)
    return binding


def approve_request(request_id: str) -> bool:
    if not db_mcp_oauth.approve_request(request_id, _stamp(_now())):
        return False
    request = db_mcp_oauth.get_request(request_id)
    events.request_approved(request["display_code"])
    return True


def deny_request(request_id: str) -> bool:
    return db_mcp_oauth.deny_request(request_id, _stamp(_now()))


def pending_requests() -> List[dict]:
    return db_mcp_oauth.list_pending_requests(_stamp(_now()))


def connected_apps() -> List[dict]:
    return db_mcp_oauth.list_active_grants(_stamp(_now()), MCP_OAUTH_CONNECTED_APPS_MAX)


def revoke_grant(grant_id: int) -> bool:
    return _revoke(grant_id, _now())


def revoke_all_remote_access() -> int:
    """Ends every connection at once: revokes all grants, closes the pairing window and drops every request. The OAuth client stays."""
    revoked = db_mcp_oauth.revoke_all(_stamp(_now()))
    invalidate()
    return revoked


def diagnostics_counts() -> dict:
    now = _stamp(_now())
    return {
        "connected_apps": db_mcp_oauth.count_active_grants(now),
        "pending_requests": db_mcp_oauth.count_pending_requests(now),
        "pairing_open": pairing_window_open(),
    }


def request_state(binding: str) -> dict:
    """What the waiting page of the browser holding this binding shows; an approved request hands its redirect over once."""
    if not _BINDING_RE.fullmatch(binding):
        return {"state": "unknown"}
    row = db_mcp_oauth.get_request_by_binding(hash_key(binding))
    if row is None:
        return {"state": "unknown"}
    if row["status"] == "denied":
        return {"state": "denied"}
    if row["expires_at"] <= _stamp(_now()):
        return {"state": "expired"}
    if row["status"] == "pending":
        return {"state": "pending", "code": row["display_code"]}
    redirect = claim_authorization(binding) if row["status"] == "approved" else None
    return {"state": "approved", "redirect": redirect} if redirect else {"state": "done"}


def claim_authorization(binding: str) -> Optional[str]:
    """Mints the authorization code for the browser that holds the binding, once, and returns the redirect URL carrying it."""
    code = secrets.token_urlsafe(32)
    now = _now()
    row = db_mcp_oauth.claim_code(hash_key(binding), hash_key(code), _stamp(now + timedelta(seconds=MCP_OAUTH_CODE_SECONDS)), _stamp(now))
    if row is None:
        return None
    return redirect_url({"code": code, "state": row["client_state"]})


def _grant_refused() -> OAuthError:
    return OAuthError("invalid_grant", _GRANT_REFUSED)


def _new_tokens(now: datetime, grant_expires: datetime) -> Tuple[List[dict], dict]:
    access = ACCESS_PREFIX + secrets.token_urlsafe(32)
    refresh = REFRESH_PREFIX + secrets.token_urlsafe(32)
    access_expires = min(now + timedelta(seconds=MCP_OAUTH_ACCESS_TOKEN_SECONDS), grant_expires)
    refresh_expires = min(now + timedelta(days=MCP_OAUTH_REFRESH_TOKEN_DAYS), grant_expires)
    rows = [
        {"hash": hash_key(access), "kind": "access", "created_at": _stamp(now), "expires_at": _stamp(access_expires)},
        {"hash": hash_key(refresh), "kind": "refresh", "created_at": _stamp(now), "expires_at": _stamp(refresh_expires)},
    ]
    body = {
        "access_token": access, "token_type": "Bearer", "expires_in": max(1, int((access_expires - now).total_seconds())),
        "refresh_token": refresh, "scope": SCOPE,
    }
    return rows, body


def _revoke(grant_id: int, now: datetime) -> bool:
    revoked = db_mcp_oauth.revoke_grant(grant_id, _stamp(now))
    invalidate(grant_id)
    return revoked


def exchange_code(code: str, redirect_uri: str, code_verifier: str, resource: Optional[str]) -> Tuple[dict, int]:
    """(token response, grant id). The code is consumed by the first presentation, whatever the checks say."""
    now = _now()
    request, reused = db_mcp_oauth.consume_code(hash_key(code), _stamp(now))
    if request is None:
        raise _grant_refused()
    if reused:
        if request["grant_id"] is not None:
            _revoke(request["grant_id"], now)
        logger.warning("An authorization code was presented twice; grant %s was revoked", request["grant_id"])
        raise _grant_refused()
    if request["code_expires_at"] <= _stamp(now) or redirect_uri != request["redirect_uri"]:
        raise _grant_refused()
    if not _VERIFIER_RE.fullmatch(code_verifier):
        raise OAuthError("invalid_grant", "The code verifier is invalid.")
    expected = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).decode().rstrip("=")
    if not hmac.compare_digest(expected, request["code_challenge"]):
        raise OAuthError("invalid_grant", "The code verifier does not match.")
    if resource is not None and resource != request["resource"]:
        raise OAuthError("invalid_target", "The resource does not match the authorization request.")
    grant_expires = now + timedelta(days=MCP_OAUTH_GRANT_DAYS)
    rows, body = _new_tokens(now, grant_expires)
    client = db_mcp_oauth.get_client()
    grant_id = db_mcp_oauth.create_grant(
        request["id"],
        {"client_id": client["client_id"], "scope": request["scope"], "resource": request["resource"], "created_at": _stamp(now), "expires_at": _stamp(grant_expires)},
        rows,
    )
    return body, grant_id


def refresh_tokens(client_id: str, refresh_token: str, scope: Optional[str], resource: Optional[str]) -> Tuple[dict, int]:
    """(token response, grant id). A refresh token presented again after its rotation revokes the whole grant, unless it is within the retry grace."""
    now = _now()
    stamped = _stamp(now)
    token_hash = hash_key(refresh_token)
    row = db_mcp_oauth.get_token(token_hash)
    if row is None or row["kind"] != "refresh" or row["client_id"] != client_id or row["revoked_at"] or row["grant_expires_at"] <= stamped:
        raise _grant_refused()
    if row["rotated_at"]:
        return _rotated_token_presented(row["grant_id"], row["rotated_at"], now)
    if row["token_expires_at"] <= stamped or row["resource"] != resource_url():
        raise _grant_refused()
    if scope is not None and (not scope.split() or set(scope.split()) - set(row["scope"].split())):
        raise OAuthError("invalid_scope", "The scope exceeds the grant.")
    if resource is not None and resource != row["resource"]:
        raise OAuthError("invalid_target", "The resource does not match the grant.")
    if not db_mcp_oauth.rotate_refresh_token(token_hash, stamped):
        # a concurrent request rotated it between our read and our update, so it was rotated within this request
        return _rotated_token_presented(row["grant_id"], stamped, now)
    rows, body = _new_tokens(now, _moment(row["grant_expires_at"]))
    db_mcp_oauth.add_tokens(row["grant_id"], rows, _stamp(now - timedelta(days=MCP_OAUTH_PRUNE_AFTER_DAYS)))
    invalidate(row["grant_id"])
    return body, row["grant_id"]


def _rotated_token_presented(grant_id: int, rotated_at: str, now: datetime):
    if rotated_at >= _stamp(now - timedelta(seconds=MCP_OAUTH_REFRESH_RETRY_GRACE_SECONDS)):
        logger.warning("A rotated refresh token was presented again within the retry grace; grant %s was kept", grant_id)
        raise _grant_refused()
    if _revoke(grant_id, now):
        events.refresh_reused()
    logger.warning("A rotated refresh token was presented again; grant %s was revoked", grant_id)
    raise _grant_refused()


def revoke_token(client_id: str, token: str) -> None:
    """RFC 7009: revoking either kind of token ends the whole grant."""
    row = db_mcp_oauth.get_token(hash_key(token))
    if row is not None and row["client_id"] == client_id:
        _revoke(row["grant_id"], _now())


def invalidate(grant_id: Optional[int] = None) -> None:
    with _lock:
        if grant_id is None:
            _cache.clear()
            _last_written.clear()
            return
        for token_hash in [h for h, (cached_grant, _) in _cache.items() if cached_grant == grant_id]:
            del _cache[token_hash]
        _last_written.pop(grant_id, None)


def _remember(token_hash: str, grant_id: int, expires: float, now: float) -> None:
    with _lock:
        for stale in [h for h, (_, until) in _cache.items() if until <= now]:
            del _cache[stale]
        if len(_cache) >= MCP_OAUTH_CACHE_MAX_ENTRIES:
            del _cache[next(iter(_cache))]
        _cache[token_hash] = (grant_id, expires)


def verify_access_token(token: str) -> Optional[int]:
    """Grant id of a valid access token for this server, else None. Reads the database on a cache miss, so call it off the event loop."""
    if not token.startswith(ACCESS_PREFIX):
        return None
    token_hash = hash_key(token)
    now = _now()
    moment = now.timestamp()
    with _lock:
        cached = _cache.get(token_hash)
        if cached is not None:
            if cached[1] > moment:
                return cached[0]
            del _cache[token_hash]
    row = db_mcp_oauth.get_token(token_hash)
    stamped = _stamp(now)
    if (
        row is None or row["kind"] != "access" or row["revoked_at"] or row["token_expires_at"] <= stamped
        or row["grant_expires_at"] <= stamped or row["resource"] != resource_url() or SCOPE not in row["scope"].split()
    ):
        return None
    _remember(token_hash, row["grant_id"], min(_epoch(row["token_expires_at"]), _epoch(row["grant_expires_at"])), moment)
    return row["grant_id"]


def _write_use(grant_id: int, used_at: str, address: str) -> None:
    try:
        db_mcp_oauth.touch_grant(grant_id, used_at, address)
    except Exception as e:
        logger.warning("Could not record the last use of OAuth grant %s: %s", grant_id, e)


def record_use(grant_id: int, address: str) -> None:
    now = _now().timestamp()
    with _lock:
        previous = _last_written.get(grant_id)
        if previous is not None and now - previous < MCP_OAUTH_LAST_USED_WRITE_SECONDS:
            return
        _last_written[grant_id] = now
    _executor.submit(_write_use, grant_id, _stamp(_now()), clean_display_text(address, 64) or "unknown")
