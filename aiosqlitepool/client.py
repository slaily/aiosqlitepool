from typing import Callable, Awaitable
from contextlib import asynccontextmanager

from .pool import Pool
from .protocols import Connection


class SQLiteConnectionPool:
    def __init__(
        self,
        connection_factory: Callable[[], Awaitable[Connection]],
        pool_size: int = 10,
        timeout: float = 30.0,
        max_idle_time: float = 600.0,
    ):
        self._pool = Pool(
            connection_factory=connection_factory,
            pool_size=pool_size,
            timeout=timeout,
            max_idle_time=max_idle_time,
        )

    @asynccontextmanager
    async def connection(self):
        conn = await self._pool.acquire()
        try:
            yield conn.raw_connection
        finally:
            await self._pool.release(conn)

    async def close(self):
        """Close the pool and all its connections."""
        await self._pool.close()
