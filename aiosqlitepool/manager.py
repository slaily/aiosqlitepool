from typing import Callable, Awaitable

from .protocols import Connection
from .connection import PoolConnection


class ConnectionManager:
    """
    Manages the lifecycle of PoolConnection objects, including creation,
    health checks, and cleanup.
    """

    def __init__(self, connection_factory: Callable[[], Awaitable[Connection]]):
        self._connection_factory = connection_factory
        self._connection_counter = 0

    async def create(self) -> PoolConnection:
        """
        Creates a new raw connection using the factory and wraps it in a
        PoolConnection.
        """
        self._connection_counter += 1
        connection_id = f"conn_{self._connection_counter}"
        raw_conn = await self._connection_factory()

        return PoolConnection(connection=raw_conn, connection_id=connection_id)

    async def close(self, conn: PoolConnection) -> None:
        """Safely closes a single connection."""
        await conn.raw_connection.close()

    async def is_alive(self, conn: PoolConnection) -> bool:
        """Checks if a connection is still usable."""
        try:
            await conn.raw_connection.execute("SELECT 1")
            return True
        except Exception:
            return False

    async def reset(self, conn: PoolConnection) -> bool:
        """
        Ensures a connection is safe for reuse by rolling back any open
        transactions. Returns True if the connection is clean, False if
        it was dirty and could not be cleaned.
        """
        try:
            await conn.raw_connection.rollback()

            return True
        except Exception:
            await self.close(conn)
            # Re-raise the original exception to inform the caller of the failure.
            raise
