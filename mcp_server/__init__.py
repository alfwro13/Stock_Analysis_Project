# GUI name: "MCP Server". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.
from . import prompts, resources, tools  # noqa: F401  registers every tool, prompt and resource on the server
from .auth import auth_failure
from .oauth_routes import PATHS as OAUTH_PATHS, ROUTES as OAUTH_ROUTES
from .public_host import PublicHostGuard, warn_if_proxy_untrusted
from .server import MCP_PATH
from .transport import endpoint, run

__all__ = ["MCP_PATH", "OAUTH_PATHS", "OAUTH_ROUTES", "PublicHostGuard", "auth_failure", "endpoint", "run", "warn_if_proxy_untrusted"]
