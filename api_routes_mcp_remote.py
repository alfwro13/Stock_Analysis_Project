from typing import Optional

import time_engine
from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Request
from pydantic import BaseModel

import config
from api_deps import limiter, require_step_up
from config import load_config, update_config_atomic
from mcp_server import audit, limits, oauth
from mcp_server.public_host import forwarded_trust, is_proxy_like
from scheduler_engine import arm_mcp_pairing_window_check

mcp_remote_router = APIRouter()

_REQUEST_ID = PathParam(pattern=r"^[A-Za-z0-9_-]{22}$")
_GRANT_ID = PathParam(ge=1, le=2**62)


def _credential(kind: str, credential_id: Optional[int]) -> str:
    if kind == "session":
        return "Dashboard"
    label = {"key": "Key", "oauth": "App"}.get(kind)
    return f"{label} #{credential_id}" if label and credential_id is not None else "None"


def _event_name(row: dict) -> Optional[str]:
    if row["credential_kind"] == "session" and row["key_id"] is not None:
        return f"{row['mcp_name']} (App #{row['key_id']})"
    return row["mcp_name"]


def _last_public_request() -> Optional[dict]:
    peer = limits.last_public_peer()
    if peer is None:
        return None
    address, at = peer
    return {"address": address, "at_display": time_engine.fmt_datetime(at), "proxy_like": is_proxy_like(address)}


def _pairing_state() -> dict:
    until = oauth.pairing_window_until()
    return {"open": until is not None, "until_display": time_engine.fmt_stored_utc(until), "seconds_left": oauth.seconds_left(until) if until else 0}


class McpRemoteToggle(BaseModel):
    enabled: bool


@mcp_remote_router.get("/mcp-remote")
@limiter.limit("120/minute")
def get_mcp_remote(request: Request):
    saved = load_config()
    host = saved.get("MCP_PUBLIC_HOST") or ""
    client = oauth.client_summary()
    return {
        "status": "success",
        "enabled": bool(saved.get("MCP_REMOTE_ENABLED")),
        "public_host": host,
        "host_active": bool(host) and host == config.MCP_PUBLIC_HOST,
        "endpoint_url": f"https://{host}/mcp" if host else None,
        "client": {"client_id": client["client_id"], "created_display": time_engine.fmt_stored_utc(client["created_at"])} if client else None,
        "pairing": _pairing_state(),
        "proxy_trust": forwarded_trust(),
        "last_public_request": _last_public_request(),
        "requests": [
            {
                "id": row["id"], "code": row["display_code"], "requested_display": time_engine.fmt_stored_utc(row["created_at"]),
                "address": row["requester_address"], "user_agent": row["user_agent"], "seconds_left": oauth.seconds_left(row["expires_at"]),
            }
            for row in oauth.pending_requests()
        ],
        "apps": [
            {
                "id": row["id"], "client_id": row["client_id"], "connected_display": time_engine.fmt_stored_utc(row["created_at"]), "expires_display": time_engine.fmt_stored_utc(row["expires_at"]),
                "last_used_display": time_engine.fmt_stored_utc(row["last_used_at"]), "last_address": row["last_address"],
            }
            for row in oauth.connected_apps()
        ],
    }


@mcp_remote_router.post("/mcp-remote/enabled")
@limiter.limit("10/minute")
def set_mcp_remote_enabled(request: Request, body: McpRemoteToggle):
    if body.enabled:
        require_step_up(request)
        if not load_config().get("MCP_PUBLIC_HOST"):
            raise HTTPException(status_code=409, detail="Save a public host first.")
        if forwarded_trust()["state"] == "wildcard":
            raise HTTPException(status_code=409, detail="FORWARDED_ALLOW_IPS is '*'. Set it to the reverse proxy's address and restart the service first.")
    update_config_atomic({"MCP_REMOTE_ENABLED": body.enabled})
    return {"status": "success", "enabled": body.enabled}


@mcp_remote_router.post("/mcp-remote/client", dependencies=[Depends(require_step_up)])
@limiter.limit("5/minute")
def create_mcp_remote_client(request: Request):
    return {"status": "success", **oauth.create_client()}


@mcp_remote_router.post("/mcp-remote/pairing", dependencies=[Depends(require_step_up)])
@limiter.limit("10/minute")
def open_mcp_pairing_window(request: Request):
    if not load_config().get("MCP_REMOTE_ENABLED"):
        raise HTTPException(status_code=409, detail="Turn on Remote Access first.")
    until = oauth.open_pairing_window()
    if until is None:
        raise HTTPException(status_code=409, detail="Create the OAuth client first.")
    arm_mcp_pairing_window_check(until)
    return {"status": "success", "pairing": _pairing_state()}


@mcp_remote_router.post("/mcp-remote/requests/{request_id}/approve", dependencies=[Depends(require_step_up)])
@limiter.limit("20/minute")
def approve_mcp_remote_request(request: Request, request_id: str = _REQUEST_ID):
    if not oauth.pairing_window_open():
        raise HTTPException(status_code=409, detail="The pairing window is closed. Open a new one first.")
    if not oauth.approve_request(request_id):
        raise HTTPException(status_code=404, detail="That request is no longer pending.")
    return {"status": "success"}


@mcp_remote_router.post("/mcp-remote/requests/{request_id}/deny")
@limiter.limit("20/minute")
def deny_mcp_remote_request(request: Request, request_id: str = _REQUEST_ID):
    if not oauth.deny_request(request_id):
        raise HTTPException(status_code=404, detail="That request is no longer pending.")
    return {"status": "success"}


@mcp_remote_router.post("/mcp-remote/grants/{grant_id}/revoke")
@limiter.limit("20/minute")
def revoke_mcp_remote_grant(request: Request, grant_id: int = _GRANT_ID):
    if not oauth.revoke_grant(grant_id):
        raise HTTPException(status_code=404, detail="That app is no longer connected.")
    audit.record_action("revoke-grant", request.client.host if request.client else None, grant_id)
    return {"status": "success"}


@mcp_remote_router.post("/mcp-remote/revoke-all")
@limiter.limit("5/minute")
def revoke_all_mcp_remote_access(request: Request):
    revoked = oauth.revoke_all_remote_access()
    audit.record_action(f"revoke-all: {revoked} grants", request.client.host if request.client else None)
    return {"status": "success", "revoked": revoked}


@mcp_remote_router.get("/mcp-remote/activity")
@limiter.limit("120/minute")
def get_mcp_remote_activity(request: Request):
    return {
        "status": "success",
        "events": [
            {
                "time_display": time_engine.fmt_stored_utc(row["logged_at"]), "credential": _credential(row["credential_kind"], row["key_id"]),
                "address": row["client_address"], "method": row["mcp_method"], "name": _event_name(row),
                "status_code": row["status"], "duration_ms": row["duration_ms"],
            }
            for row in audit.recent_events()
        ],
    }
