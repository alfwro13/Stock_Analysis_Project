import logging
import os
import secrets
from typing import Optional

from starlette.requests import Request
from starlette.responses import JSONResponse

from config import load_config

from .server import MCP_PATH

logger = logging.getLogger(__name__)

KEY_ENV = "MCP_API_KEY"


def auth_failure(request: Request) -> Optional[JSONResponse]:
    if not load_config().get("MCP_SERVER_ENABLED"):
        return JSONResponse({"detail": "The MCP Server is disabled"}, status_code=503)
    if request.headers.get("X-API-Key"):
        return JSONResponse({"detail": "The API key cannot be used on this route"}, status_code=403)
    expected = os.environ.get(KEY_ENV, "")
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if expected and scheme.lower() == "bearer" and secrets.compare_digest(token.strip().encode(), expected.encode()):
        return None
    if not expected:
        logger.warning("Refused a %s request: %s is not set", MCP_PATH, KEY_ENV)
    return JSONResponse({"detail": "Invalid or missing MCP key"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
