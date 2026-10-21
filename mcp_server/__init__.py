# GUI name: "MCP Server". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.
from . import prompts, resources, tools  # noqa: F401  registers every tool, prompt and resource on the server
from .auth import auth_failure
from .public_host import PublicHostGuard
from .server import MCP_PATH
from .transport import endpoint, run

__all__ = ["MCP_PATH", "PublicHostGuard", "auth_failure", "endpoint", "run"]
