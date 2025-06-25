import time

from .protocols import Connection


class PoolConnection:
    """
    Represents a connection managed by the pool, holding the raw connection
    and its metadata.
    """

    def __init__(self, connection: Connection, connection_id: str):
        self.raw_connection = connection
        self.id = connection_id
        self.created_at = time.time()

    @property
    def age(self) -> float:
        """The age of the connection in seconds."""
        return time.time() - self.created_at

    @property
    def is_closed(self) -> bool:
        """Check if the underlying aiosqlite connection is closed."""
        # aiosqlite's public API for this is checking the _connection attribute.
        # When closed, it's not None.
        return self.raw_connection._connection is not None
