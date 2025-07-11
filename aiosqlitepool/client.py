from typing import Callable, Awaitable, Optional
from contextlib import asynccontextmanager

from .pool import Pool
from .protocols import Connection


class SQLiteConnectionPool:
    def __init__(
        self,
        connection_factory: Callable[[], Awaitable[Connection]],
        pool_size: Optional[int] = 5,
        acquisition_timeout: Optional[int] = 30,
        idle_timeout: Optional[int] = 86400,
        operation_timeout: Optional[int] = 10,
    ):
        """
        Initializes the high-level connection pool manager.

        Args:
            connection_factory: An async callable that returns a new raw
                database connection.
            pool_size (int, optional): The maximum number of connections to keep
                in the pool. Defaults to 5.
            acquisition_timeout (int, optional): The maximum number of seconds to
                wait for a connection to become available before raising a
                timeout error. Defaults to 30.
            idle_timeout (int, optional): The maximum number of seconds that a
                connection can remain idle in the pool before being closed and
                replaced. This helps prevent issues with firewalls or database
                servers closing stale connections. Set this to a value lower
                than your database's idle timeout setting. Defaults to 86400.
        """
        self._pool = Pool(
            connection_factory=connection_factory,
            pool_size=pool_size,
            acquisition_timeout=int(acquisition_timeout),
            idle_timeout=int(idle_timeout),
            operation_timeout=int(operation_timeout),
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

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()
