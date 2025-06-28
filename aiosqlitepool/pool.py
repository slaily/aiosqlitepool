import asyncio
import logging
from typing import Dict, Callable, Awaitable, Optional

from .connection import PoolConnection
from .protocols import Connection


log = logging.getLogger(__name__)


class Pool:
    def __init__(
        self,
        connection_factory: Callable[[], Awaitable[Connection]],
        pool_size: Optional[int] = 20,
        acquisition_timeout: Optional[int] = 30,
        idle_timeout: Optional[int] = 86400,
        operation_timeout: Optional[int] = 10,
    ):
        """
        Initializes a new connection pool.

        Args:
            connection_factory: An async callable that returns a new Connection object.
            pool_size (int, optional): The maximum number of connections to keep
                in the pool. Defaults to 20.
            acquisition_timeout (int, optional): The maximum number of seconds to
                wait for a connection to become available before raising a
                timeout error. Defaults to 30.
            idle_timeout (int, optional): The maximum number of seconds that a
                connection can remain idle in the pool before being closed and
                replaced. This helps prevent issues with firewalls or database
                servers closing stale connections. Defaults to 86400.
            operation_timeout(int, optional): The maximum number of seconds to
                wait for a connection operation (e.g., reset, close) to complete.
                Defaults to 10.
        """
        self._connection_factory = connection_factory
        self._pool_size = pool_size
        self._acquisition_timeout = acquisition_timeout
        self._idle_timeout = idle_timeout
        self._operation_timeout = operation_timeout

        self._queue: asyncio.Queue[PoolConnection] = asyncio.Queue(maxsize=pool_size)
        self._connection_registry: Dict[str, PoolConnection] = {}
        self._lock = asyncio.Lock()
        self._closed_event = asyncio.Event()

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
            await self._retire_connection(conn)
            return

        to_close = None
        async with self._lock:
            # If the connection is no longer tracked, do nothing.
            if conn.id not in self._connection_registry:
                return

            try:
                # Try to reset the connection to a clean state.
                # This is done inside the lock to prevent a race condition
                # where the connection is closed by another task (e.g. pool.close())
                # after we reset it but before we return it to the queue.
                await asyncio.wait_for(conn.reset(), timeout=self._operation_timeout)

                # Mark it as idle and return it to the queue.
                conn.mark_as_idle()
                self._queue.put_nowait(conn)
            except asyncio.QueueFull:
                # This should not happen if pool size is managed correctly,
                # but as a safeguard, we retire the connection.
                self._connection_registry.pop(conn.id, None)
                to_close = conn
            except Exception:
                # If reset fails, the connection is considered broken.
                # We must retire it completely.
                self._connection_registry.pop(conn.id, None)
                to_close = conn

        # Perform the actual closing outside the lock to avoid holding
        # the lock during a potentially slow I/O operation.
        if to_close:
            try:
                await asyncio.wait_for(
                    to_close.close(), timeout=self._operation_timeout
                )
            except (asyncio.TimeoutError, Exception) as e:
                log.warning("Failed to close retired connection %s: %s", to_close.id, e)

    async def close(self):
        if self.is_closed:
            return None

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
        close_tasks = [
            asyncio.wait_for(conn.close(), timeout=self._operation_timeout)
            for conn in connections
        ]
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)

    async def _claim_if_healthy(self, conn: PoolConnection) -> bool:
        if conn.idle_time > self._idle_timeout:
            return False

        try:
            await asyncio.wait_for(conn.is_alive(), timeout=self._operation_timeout)
        except (asyncio.TimeoutError, Exception):
            return False

        conn.mark_as_in_use()
        return True

    async def _retire_connection(self, conn: PoolConnection):
        """Close a connection and remove it from the registry."""
        try:
            await asyncio.wait_for(conn.close(), timeout=self._operation_timeout)
        except (asyncio.TimeoutError, Exception):
            # The connection is likely broken, but we must ensure it's removed
            # from the registry.
            pass
        finally:
            async with self._lock:
                self._connection_registry.pop(conn.id, None)

    async def _try_provision_new_connection(self) -> PoolConnection:
        async with self._lock:
            if len(self._connection_registry) < self._pool_size:
                new_conn = None
                try:
                    new_conn = await PoolConnection.create(self._connection_factory)
                    # Run a health check on the new connection before adding it.
                    await asyncio.wait_for(
                        new_conn.is_alive(), timeout=self._operation_timeout
                    )
                    self._connection_registry[new_conn.id] = new_conn
                    new_conn.mark_as_in_use()
                    return new_conn
                except (asyncio.TimeoutError, Exception) as e:
                    log.warning("Failed to provision new connection: %s", e)
                    if new_conn:
                        # Best-effort cleanup of the failed connection
                        try:
                            await asyncio.wait_for(
                                new_conn.close(), timeout=self._operation_timeout
                            )
                        except Exception:
                            pass
                    return None
        return None

    async def _wait_for_healthy_connection(self) -> PoolConnection:
        while True:
            get_task = asyncio.create_task(self._queue.get())
            close_task = asyncio.create_task(self._closed_event.wait())

            # done/pending are sets of tasks
            done, pending = set(), {get_task, close_task}
            try:
                done, pending = await asyncio.wait(
                    {get_task, close_task}, return_when=asyncio.FIRST_COMPLETED
                )

                if close_task in done:
                    # This will be cleaned up in the finally block
                    raise RuntimeError("Pool is closed.")

                conn = get_task.result()

                if await self._claim_if_healthy(conn):
                    return conn

                # Connection was not healthy, retire it and loop to wait for another.
                await self._retire_connection(conn)

            finally:
                # The finally block ensures that pending tasks are
                # always cancelled, preventing orphaned tasks.
                for task in pending:
                    task.cancel()
                    try:
                        # We must await the task to allow it to be cancelled.
                        await task
                    except asyncio.CancelledError:
                        # This is the expected exception when cancelling a task.
                        pass

    async def _run_acquisition_cycle(self) -> PoolConnection:
        if self.is_closed:
            raise RuntimeError("Pool is closed.")

        # Phase 1: Try to get an idle connection from the queue immediately.
        while not self._queue.empty():
            try:
                conn = self._queue.get_nowait()
                if await self._claim_if_healthy(conn):
                    return conn
                # Stale connection, retire and try next one in queue
                await self._retire_connection(conn)
            except asyncio.QueueEmpty:
                break  # Queue is empty, move to next phase

        # Phase 2: If the queue was empty or all connections were stale,
        # try to create a new one if there is capacity.
        conn = await self._try_provision_new_connection()
        if conn:
            return conn

        # Phase 3: Pool is full. Wait for a connection to be released.
        # This function now guarantees a healthy connection.
        return await self._wait_for_healthy_connection()
