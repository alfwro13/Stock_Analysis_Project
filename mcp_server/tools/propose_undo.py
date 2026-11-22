import time
from typing import Annotated, Any

from pydantic import Field

from constants import MCP_WRITE_ID_ARG_MAX_CHARS

from .. import writes
from ..envelope import envelope
from ..server import CHANGE_PROPOSAL, server

_UNITS = {
    "change_id": "the change_id confirm_change returned for the change to reverse",
    "expires_at": "ISO UTC: the proposal is refused after this time",
}


@server.tool(name="propose_undo", annotations=CHANGE_PROPOSAL)
def propose_undo(change_id: Annotated[str, Field(max_length=MCP_WRITE_ID_ARG_MAX_CHARS)]) -> dict[str, Any]:
    """Proposes reversing a change this connection made earlier (the change_id confirm_change returned). It is a proposal like any other: nothing changes until the user approves and confirm_change is called. Refused when the data changed since. Needs a connection with changes enabled in Settings."""
    proposal = writes.propose_undo(change_id)
    return envelope(proposal, as_of=time.time(), units=_UNITS, missing=[])
