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
        pool_size: int = 5,
        acquisition_timeout: float = 30.0,
        max_idle_time: float = 3600.0,
        max_consecutive_failures: int = 5,
    ):
        self._connection_factory = connection_factory
        self._pool_size = pool_size
        self._acquisition_timeout = acquisition_timeout
        self._max_idle_time = max_idle_time
        self._max_consecutive_failures = max_consecutive_failures

        self._queue: asyncio.Queue[PoolConnection] = asyncio.Queue(maxsize=pool_size)
        self._connection_registry: Dict[str, PoolConnection] = {}
        self._lock = asyncio.Lock()
        self._closed_event = asyncio.Event()
        self._consecutive_failures = 0

    @property
    def is_closed(self) -> bool:
        return self._closed_event.is_set()

    @property
    def size(self) -> int:
        return self._queue.qsize()

    async def acquire(self) -> PoolConnection:
        try:
            return await asyncio.wait_for(
                self._run_acquisition_cycle(), timeout=self._acquisition_timeout
            )
        except asyncio.TimeoutError:
            raise RuntimeError(
                f"Pool timeout: Failed to acquire connection within {self._acquisition_timeout} seconds"
            )

    async def release(self, conn: PoolConnection):
        if self.is_closed:
            async with self._lock:
                if conn.id in self._connection_registry:
                    await self._decommission_connection(conn)
            return

        async with self._lock:
            if conn.id not in self._connection_registry:
                return

        # Try to reset connection
        try:
            await asyncio.wait_for(
                conn.reset(), timeout=min(self._acquisition_timeout, 5.0)
            )
        except Exception as e:
            log.warning(
                "Connection %s failed to reset and will be discarded.",
                conn.id,
                exc_info=e,
            )
            # Connection couldn't be reset, clean it up
            async with self._lock:
                await self._decommission_connection(conn)
            return

        # Connection successfully reset, return to pool
        conn.mark_as_idle()
        try:
            self._queue.put_nowait(conn)
        except asyncio.QueueFull:
            # Pool is full (shouldn't happen), clean up connection
            async with self._lock:
                await self._decommission_connection(conn)

    async def close(self):
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
            connections = list(self._connection_registry.values())
            self._connection_registry.clear()

        # Close all connections concurrently
        close_tasks = [conn.close() for conn in connections]
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)

    async def _claim_if_healthy(self, conn: PoolConnection) -> bool:
        if conn.idle_time > (self._max_idle_time - 0.1):
            return False

        await conn.is_alive()
        conn.mark_as_in_use()

        return True

    async def _decommission_connection(self, conn: PoolConnection):
        try:
            await conn.close()
        finally:
            self._connection_registry.pop(conn.id, None)

    async def _poll_for_healthy_connection(self) -> PoolConnection:
        if self.is_closed:
            return None

        while True:
            try:
                conn = self._queue.get_nowait()
                if await self._claim_if_healthy(conn):
                    return conn
                # Invalid connection - clean up and try again
                async with self._lock:
                    await self._decommission_connection(conn)
            except asyncio.QueueEmpty:
                return None

    async def _try_provision_new_connection(self) -> PoolConnection:
        async with self._lock:
            if len(self._connection_registry) < self._pool_size:
                conn = await PoolConnection.create(self._connection_factory)
                # Run a health check on the new connection before adding it.
                await conn.is_alive()
                self._connection_registry[conn.id] = conn
                conn.mark_as_in_use()
                return conn
        return None

    async def _wait_for_connection_or_close(self) -> PoolConnection:
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

    async def _run_acquisition_cycle(self) -> PoolConnection:
        if self.is_closed:
            raise RuntimeError("Pool is closed.")

        while True:
            try:
                conn = await self._poll_for_healthy_connection()
                if conn:
                    async with self._lock:
                        self._consecutive_failures = 0  # Reset on success
                    return conn

                conn = await self._try_provision_new_connection()
                if conn:
                    async with self._lock:
                        self._consecutive_failures = 0  # Reset on success
                    return conn

            except Exception as e:
                async with self._lock:
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
            if await self._claim_if_healthy(conn):
                async with self._lock:
                    self._consecutive_failures = 0  # Reset on success
                return conn

            # Connection was invalid, clean up and retry
            async with self._lock:
                await self._decommission_connection(conn)
