import asyncio
import logging
from typing import Dict

from .connection import PoolConnection
from .manager import ConnectionManager

logger = logging.getLogger(__name__)


class Pool:
    """
    A pure pooling engine that manages connections and handles acquisition logic.

    Philosophy: "Fail Fast for Users, Fail Safe for Pool"
    - User-facing operations preserve original driver exceptions
    - Internal operations handle errors gracefully with specific logging
    """

    def __init__(
        self,
        manager: ConnectionManager,
        pool_size: int = 10,
        timeout: float = 30.0,
        max_idle_time: float = 600.0,
    ):
        self._manager = manager
        self._pool_size = pool_size
        self._timeout = timeout
        self._max_idle_time = max_idle_time

        self._queue: asyncio.Queue[PoolConnection] = asyncio.Queue(maxsize=pool_size)
        self._all_connections: Dict[str, PoolConnection] = {}
        self._lock = asyncio.Lock()
        self._closed_event = asyncio.Event()

    @property
    def size(self) -> int:
        """Current number of available connections in the pool."""
        return self._queue.qsize() if not self.is_closed else 0

    @property
    def total_connections(self) -> int:
        """Total number of connections (both idle and active)."""
        return len(self._all_connections)

    @property
    def is_closed(self) -> bool:
        """Whether the pool is closed."""
        return self._closed_event.is_set()

    async def _validate_and_prepare_connection(self, conn: PoolConnection) -> bool:
        """Validate connection and mark as in-use if valid, close if invalid."""
        try:
            # Check idle time with small tolerance for race conditions
            if conn.idle_time > (self._max_idle_time - 0.1):
                logger.debug("Connection %s exceeded max idle time", conn.id)
                return False

            # Validate connection with timeout
            if not await asyncio.wait_for(self._manager.is_alive(conn), timeout=5.0):
                return False

            conn.mark_as_in_use()
            return True

        except Exception as e:
            logger.debug(
                "Connection %s validation failed: %s", conn.id, type(e).__name__
            )
            return False

    async def _cleanup_connection(self, conn: PoolConnection):
        """Close connection and remove from tracking. Always succeeds."""
        try:
            await self._manager.close(conn)
        except Exception as e:
            logger.debug("Error closing connection %s: %s", conn.id, type(e).__name__)
        finally:
            self._all_connections.pop(conn.id, None)

    async def _try_get_from_queue(self) -> PoolConnection:
        """Try to get a valid connection from queue, cleaning up invalid ones."""
        if self.is_closed:
            return None

        while True:
            try:
                conn = self._queue.get_nowait()
                if await self._validate_and_prepare_connection(conn):
                    return conn
                # Invalid connection - clean up and try again
                async with self._lock:
                    await self._cleanup_connection(conn)
            except asyncio.QueueEmpty:
                return None

    async def _create_connection_if_space(self) -> PoolConnection:
        """Create new connection if under pool limit."""
        async with self._lock:
            if len(self._all_connections) < self._pool_size:
                conn = await self._manager.create()
                self._all_connections[conn.id] = conn
                conn.mark_as_in_use()
                return conn
        return None

    async def _wait_for_connection_or_close(self) -> PoolConnection:
        """Wait for connection to be available or pool to close."""
        get_task = asyncio.create_task(self._queue.get())
        close_task = asyncio.create_task(self._closed_event.wait())

        try:
            done, pending = await asyncio.wait(
                [get_task, close_task], return_when=asyncio.FIRST_COMPLETED
            )

            # Cancel pending tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            if close_task in done:
                raise RuntimeError("Pool is closed.")

            return get_task.result()

        except Exception:
            # Clean up tasks on error
            for task in [get_task, close_task]:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            raise

    async def _acquire_internal(self) -> PoolConnection:
        """Internal acquire method without timeout wrapper."""
        if self.is_closed:
            raise RuntimeError("Pool is closed.")

        while True:
            # Try to get from queue first
            conn = await self._try_get_from_queue()
            if conn:
                return conn

            # Try to create new connection
            conn = await self._create_connection_if_space()
            if conn:
                return conn

            # Wait for connection or pool close
            conn = await self._wait_for_connection_or_close()
            if await self._validate_and_prepare_connection(conn):
                return conn

            # Connection was invalid, clean up and retry
            async with self._lock:
                await self._cleanup_connection(conn)

    async def acquire(self) -> PoolConnection:
        """Acquire a connection from the pool."""
        try:
            return await asyncio.wait_for(
                self._acquire_internal(), timeout=self._timeout
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Pool timeout: Failed to acquire connection within {self._timeout} seconds"
            )

    async def release(self, conn: PoolConnection):
        """Release a connection back to the pool."""
        if self.is_closed:
            async with self._lock:
                if conn.id in self._all_connections:
                    await self._cleanup_connection(conn)
            return

        async with self._lock:
            if conn.id not in self._all_connections:
                return

        # Try to reset connection
        try:
            reset_ok = await asyncio.wait_for(
                self._manager.reset(conn), timeout=min(self._timeout, 5.0)
            )
        except Exception as e:
            logger.warning(
                "Reset failed for connection %s: %s", conn.id, type(e).__name__
            )
            reset_ok = False

        if not reset_ok:
            async with self._lock:
                await self._cleanup_connection(conn)
            return

        # Return to pool
        conn.mark_as_idle()
        try:
            self._queue.put_nowait(conn)
        except asyncio.QueueFull:
            async with self._lock:
                await self._cleanup_connection(conn)

    async def close(self):
        """Close all connections in the pool."""
        if self.is_closed:
            return

        self._closed_event.set()

        try:
            await asyncio.wait_for(self._close_all_connections(), timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning("Pool close timed out after 30s")

    async def _close_all_connections(self):
        """Close all connections with proper cleanup."""
        async with self._lock:
            # Drain queue
            remaining = []
            while not self._queue.empty():
                try:
                    remaining.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break

            # Add all tracked connections
            remaining.extend(self._all_connections.values())

            # Close all in parallel
            if remaining:
                await asyncio.gather(
                    *[self._cleanup_connection(conn) for conn in remaining],
                    return_exceptions=True,
                )

            self._all_connections.clear()
