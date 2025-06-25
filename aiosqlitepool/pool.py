import asyncio
import time

from .connection import PoolConnection
from .manager import ConnectionManager


class Pool:
    """
    A pure pooling engine that manages a queue of available connections and
    handles the logic of waiting for a connection to become available.
    """

    def __init__(
        self,
        manager: ConnectionManager,
        pool_size: int = 10,
        timeout: float = 30.0,
        max_connection_age: float = 3600.0,
        max_idle_time: float = 600.0,
    ):
        self._manager = manager
        self._pool_size = pool_size
        self._timeout = timeout
        self._max_connection_age = max_connection_age
        self._max_idle_time = max_idle_time

        self._pool: asyncio.Queue[PoolConnection] = asyncio.Queue(maxsize=pool_size)
        self._all_connections: dict[str, PoolConnection] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    @property
    def size(self) -> int:
        """The current number of available connections in the pool."""
        return self._pool.qsize()

    async def acquire(self) -> PoolConnection:
        """Acquire a connection from the pool."""
        if self._closed:
            raise RuntimeError("Pool is closed.")

        start_time = time.time()

        while True:
            # Calculate remaining timeout
            elapsed = time.time() - start_time
            if elapsed >= self._timeout:
                raise asyncio.TimeoutError(f"Pool timeout after {self._timeout:.2f}s")
            remaining_timeout = self._timeout - elapsed

            # Try to get a connection from the queue
            try:
                conn = self._pool.get_nowait()

                # Check if the recycled connection is still valid
                is_alive = await self._manager.is_alive(conn)
                is_stale = conn.age > self._max_connection_age

                if is_alive and not is_stale:
                    return conn
                else:
                    # If it's dead or stale, close it and try again
                    await self._manager.close(conn)
                    self._all_connections.pop(conn.id, None)
                    continue  # Re-enter the loop to get/create a new one
            except asyncio.QueueEmpty:
                pass  # Queue is empty, proceed to create a new one if possible

            # If queue was empty, try to create a new connection
            async with self._lock:
                if len(self._all_connections) < self._pool_size:
                    try:
                        conn = await self._manager.create()
                        self._all_connections[conn.id] = conn
                        return conn
                    except Exception:
                        # If creation fails, we don't want to block forever
                        raise

            # If pool is full, wait for a connection to be released
            try:
                conn = await asyncio.wait_for(
                    self._pool.get(), timeout=remaining_timeout
                )
                # Re-run checks on the newly acquired connection
                if await self._manager.is_alive(conn):
                    return conn
                else:
                    await self._manager.close(conn)
                    self._all_connections.pop(conn.id, None)
                    # Loop again to get a valid one
            except asyncio.TimeoutError:
                raise asyncio.TimeoutError(
                    f"Failed to acquire connection in {self._timeout:.2f}s (pool full)."
                )

    def release(self, conn: PoolConnection):
        """Release a connection back to the pool."""
        if not self._closed:
            try:
                self._pool.put_nowait(conn)
            except asyncio.QueueFull:
                # This can happen if a connection is released after the pool is "full"
                # (e.g., in a race condition with close()). In this case, just close it.
                asyncio.create_task(self._manager.close(conn))
                self._all_connections.pop(conn.id, None)

    async def close(self):
        """Close all connections in the pool."""
        if self._closed:
            return
        self._closed = True

        async with self._lock:
            # Close all connections currently in the queue
            while not self._pool.empty():
                conn = self._pool.get_nowait()
                await self._manager.close(conn)

            # Clear the tracking dict
            self._all_connections.clear()
