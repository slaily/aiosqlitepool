import asyncio
import logging
from typing import Dict, Callable, Awaitable

from .connection import PoolConnection
from .protocols import Connection


log = logging.getLogger(__name__)


class PoolUnhealthyError(RuntimeError):
    """Raised when the pool's circuit breaker trips."""


class Pool:
    def __init__(
        self,
        connection_factory: Callable[[], Awaitable[Connection]],
        pool_size: int = 20,
        timeout: float = 30.0,
        max_idle_time: float = 3600.0,
        max_consecutive_failures: int = 5,
    ):
        self._connection_factory = connection_factory
        self._pool_size = pool_size
        self._timeout = timeout
        self._max_idle_time = max_idle_time
        self._max_consecutive_failures = max_consecutive_failures

        self._queue: asyncio.Queue[PoolConnection] = asyncio.Queue(maxsize=pool_size)
        self._all_connections: Dict[str, PoolConnection] = {}
        self._lock = asyncio.Lock()
        self._closed_event = asyncio.Event()
        self._consecutive_failures = 0

    @property
    def is_closed(self) -> bool:
        """Whether the pool is closed."""
        return self._closed_event.is_set()

    @property
    def size(self) -> int:
        """Current number of available connections in the pool."""
        return self._queue.qsize()

    async def _validate_and_prepare_connection(self, conn: PoolConnection) -> bool:
        """Validate connection and mark as in-use if valid, close if invalid."""
        # Check idle time with small tolerance for race conditions
        if conn.idle_time > (self._max_idle_time - 0.1):
            return False

        # Validate connection with timeout
        await conn.is_alive()
        conn.mark_as_in_use()

        return True

    async def _cleanup_connection(self, conn: PoolConnection):
        """Close connection and remove from tracking. Always succeeds."""
        try:
            await conn.close()
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
        """Create new connection if under pool limit, ensuring it is valid."""
        async with self._lock:
            if len(self._all_connections) < self._pool_size:
                conn = await PoolConnection.create(self._connection_factory)
                # Run a health check on the new connection before adding it.
                await conn.is_alive()
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
            try:
                # Try to get from queue first
                conn = await self._try_get_from_queue()
                if conn:
                    self._consecutive_failures = 0  # Reset on success
                    return conn

                # Try to create new connection
                conn = await self._create_connection_if_space()
                if conn:
                    self._consecutive_failures = 0  # Reset on success
                    return conn

            except Exception as e:
                self._consecutive_failures += 1
                log.debug("Connection acquisition failed.", exc_info=e)
                if self._consecutive_failures >= self._max_consecutive_failures:
                    msg = (
                        f"Pool has exceeded max consecutive failures "
                        f"({self._max_consecutive_failures})."
                    )
                    raise PoolUnhealthyError(msg) from e

                # If we are here, we are in the self-healing phase.
                # The failed connection was already cleaned up. Continue the loop.
                continue

            # Wait for connection or pool close
            conn = await self._wait_for_connection_or_close()
            if await self._validate_and_prepare_connection(conn):
                self._consecutive_failures = 0  # Reset on success
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
            await asyncio.wait_for(conn.reset(), timeout=min(self._timeout, 5.0))
        except Exception as e:
            log.warning(
                "Connection %s failed to reset and will be discarded.",
                conn.id,
                exc_info=e,
            )
            # Connection couldn't be reset, clean it up
            async with self._lock:
                await self._cleanup_connection(conn)
            return

        # Connection successfully reset, return to pool
        conn.mark_as_idle()
        try:
            self._queue.put_nowait(conn)
        except asyncio.QueueFull:
            # Pool is full (shouldn't happen), clean up connection
            async with self._lock:
                await self._cleanup_connection(conn)

    async def close(self):
        """Close the pool and all connections."""
        if self.is_closed:
            return

        self._closed_event.set()

        # Clear the queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

        async with self._lock:
            connections = list(self._all_connections.values())
            self._all_connections.clear()

        # Close all connections concurrently
        close_tasks = [conn.close() for conn in connections]
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
