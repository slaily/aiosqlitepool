from typing import Callable, Awaitable
from contextlib import asynccontextmanager

from .pool import Pool
from .protocols import Connection


class SQLiteConnectionPool:
    def __init__(
        self,
        connection_factory: Callable[[], Awaitable[Connection]],
        pool_size: int = 10,
        acquisition_timeout: float = 30.0,
        idle_timeout: float = 600.0,
    ):
        """
        Initializes the high-level connection pool manager.

        Args:
            connection_factory: An async callable that returns a new raw
                database connection.
            pool_size: The maximum number of connections to keep in the pool.
            acquisition_timeout: The maximum number of seconds to wait for a
                connection to become available before raising a timeout error.
            idle_timeout: The maximum number of seconds that a connection can remain
                idle in the pool before being closed and replaced. This helps
                prevent issues with firewalls or database servers closing stale
                connections. Set this to a value lower than your database's
                idle timeout setting.
        """
        self._pool = Pool(
            connection_factory=connection_factory,
            pool_size=pool_size,
            acquisition_timeout=acquisition_timeout,
            idle_timeout=idle_timeout,
        )

    @asynccontextmanager
    async def connection(self):
        conn = await self._pool.acquire()
        try:
            yield conn.raw_connection
        finally:
            await self._pool.release(conn)

    async def close(self):
        await self._pool.close()
