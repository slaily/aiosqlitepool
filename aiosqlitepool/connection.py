import time
from typing import Optional

from .protocols import Connection


class PoolConnection:
    """
    Represents a connection managed by the pool, holding the raw connection
    and its metadata.
    """

    def __init__(self, connection: Connection, connection_id: str):
        self.raw_connection = connection
        self.id = connection_id
        self.idle_since: Optional[float] = None

    def mark_as_in_use(self) -> None:
        """Mark the connection as actively in use."""
        self.idle_since = None

    def mark_as_idle(self) -> None:
        """Mark the connection as idle."""
        self.idle_since = time.time()

    @property
    def idle_time(self) -> float:
        """The time the connection has been idle in seconds."""
        if self.idle_since is None:
            return 0.0
        return time.time() - self.idle_since
