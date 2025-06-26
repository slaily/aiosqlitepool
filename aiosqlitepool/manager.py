import logging
from typing import Callable, Awaitable

from .protocols import Connection
from .connection import PoolConnection

# Create logger for specific error tracking
logger = logging.getLogger(__name__)


class ConnectionManager:
    """
    Manages the lifecycle of PoolConnection objects, including creation,
    health checks, and cleanup.

    Philosophy:
    - create(): Let all driver exceptions bubble up to user
    - is_alive()/reset(): Return success/failure, log specific errors
    - close(): Best effort cleanup, log errors but don't raise
    """

    def __init__(self, connection_factory: Callable[[], Awaitable[Connection]]):
        self._connection_factory = connection_factory
        self._connection_counter = 0

    async def create(self) -> PoolConnection:
        """
        User-facing operation: Create a new connection.
        All driver exceptions bubble up unchanged - no try/except wrapper.
        """
        self._connection_counter += 1
        connection_id = f"conn_{self._connection_counter}"
        # Let all driver exceptions bubble up naturally
        raw_conn = await self._connection_factory()

        return PoolConnection(connection=raw_conn, connection_id=connection_id)

    async def close(self, conn: PoolConnection) -> None:
        """
        Internal operation: Safely close a connection.
        Always succeeds - logs errors but doesn't raise.
        """
        try:
            await conn.raw_connection.close()
        except Exception as e:
            # Log specific exception but don't re-raise - this is cleanup
            logger.debug(
                "Close failed for connection %s: %s: %s",
                conn.id,
                type(e).__name__,
                str(e),
            )

    async def is_alive(self, conn: PoolConnection) -> bool:
        """
        Internal operation: Check if a connection is still usable.
        Returns False for any error, logs specific exception details.
        """
        try:
            await conn.raw_connection.execute("SELECT 1")
            return True
        except Exception as e:
            # Log specific exception type and message for debugging
            logger.debug(
                "Health check failed for connection %s: %s: %s",
                conn.id,
                type(e).__name__,
                str(e),
            )
            return False

    async def reset(self, conn: PoolConnection) -> bool:
        """
        Internal operation: Reset connection state for reuse.
        Returns True if successful, False if connection should be discarded.
        Logs specific errors but doesn't raise.
        """
        try:
            await conn.raw_connection.rollback()
            return True
        except Exception as e:
            # Log specific exception for debugging
            logger.debug(
                "Reset failed for connection %s: %s: %s",
                conn.id,
                type(e).__name__,
                str(e),
            )
            return False
