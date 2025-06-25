import aiosqlite
import logging

from .factory import ConnectionFactory
from .connection import PoolConnection

logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages the lifecycle of PoolConnection objects, including creation,
    health checks, and cleanup.
    """

    def __init__(self, factory: ConnectionFactory):
        self._factory = factory
        self._connection_counter = 0

    async def create(self) -> PoolConnection:
        """
        Creates a new raw connection using the factory and wraps it in a
        PoolConnection.
        """
        self._connection_counter += 1
        connection_id = f"conn_{self._connection_counter}"
        raw_conn = await self._factory.create()
        return PoolConnection(connection=raw_conn, connection_id=connection_id)

    async def close(self, conn: PoolConnection) -> None:
        """Safely closes a single connection."""
        try:
            await conn.raw_connection.close()
        except Exception as e:
            logger.error(f"Error closing connection {conn.id}: {e}")

    async def is_alive(self, conn: PoolConnection) -> bool:
        """Checks if a connection is still usable."""
        try:
            cursor = await conn.raw_connection.execute("SELECT 1")
            await cursor.fetchone()
            return True
        except (aiosqlite.Error, ValueError):
            return False

    async def reset(self, conn: PoolConnection) -> bool:
        """
        Ensures a connection is safe for reuse by rolling back any open
        transactions. Returns True if the connection is clean, False if
        it was dirty and could not be cleaned.
        """
        if conn.raw_connection.in_transaction:
            try:
                await conn.raw_connection.rollback()
            except Exception as e:
                logger.warning(
                    f"Failed to rollback connection {conn.id}, closing it: {e}"
                )
                await self.close(conn)
                return False
        return True
