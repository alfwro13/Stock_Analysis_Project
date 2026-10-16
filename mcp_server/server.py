from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

MCP_PATH = "/mcp"

server = MCPServer(
    "MCP Server",
    instructions=(
        "Read-only view of the Quantamental Dashboard. Every tool response carries as_of (when the data was "
        "produced), market_state, units, and stale/missing markers. A null value means unavailable, never zero."
    ),
)

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)
