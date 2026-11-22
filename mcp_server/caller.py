from contextvars import ContextVar
from typing import NamedTuple, Optional


class Caller(NamedTuple):
    kind: str
    credential_id: int
    address: str

    @property
    def label(self) -> str:
        return f"key #{self.credential_id}" if self.kind == "key" else f"remote grant #{self.credential_id}"


current: ContextVar[Optional[Caller]] = ContextVar("mcp_caller", default=None)
