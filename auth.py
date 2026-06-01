import os
import secrets
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

security = HTTPBasic(auto_error=False)


def require_auth(
    request: Request,
    credentials: HTTPBasicCredentials = Depends(security),
):
    # Method 1: API key header (scripts / curl)
    api_key = request.headers.get("X-API-Key", "")
    if api_key:
        expected = os.environ.get("API_KEY", "")
        if expected and secrets.compare_digest(api_key.encode(), expected.encode()):
            return
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

    # Method 2: HTTP Basic Auth (browser)
    if credentials:
        valid_user = secrets.compare_digest(
            credentials.username.encode(),
            os.environ.get("DASHBOARD_USERNAME", "").encode(),
        )
        valid_pass = secrets.compare_digest(
            credentials.password.encode(),
            os.environ.get("DASHBOARD_PASSWORD", "").encode(),
        )
        if valid_user and valid_pass:
            return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        headers={"WWW-Authenticate": "Basic"},
    )
