import logging
from typing import Optional

from starlette.requests import Request
from starlette.responses import JSONResponse

from config import load_config

from . import keys
from .server import MCP_PATH

logger = logging.getLogger(__name__)


def auth_failure(request: Request) -> Optional[JSONResponse]:
    if not load_config().get("MCP_SERVER_ENABLED"):
        return JSONResponse({"detail": "The MCP Server is disabled"}, status_code=503)
    if request.headers.get("X-API-Key"):
        return JSONResponse({"detail": "The API key cannot be used on this route"}, status_code=403)
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        key_id = keys.match(token.strip())
        if key_id is not None:
            keys.record_use(key_id)
            return None
    if not keys.has_keys():
        logger.warning("Refused a %s request: no MCP key exists", MCP_PATH)
    return JSONResponse({"detail": "Invalid or missing MCP key"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})
