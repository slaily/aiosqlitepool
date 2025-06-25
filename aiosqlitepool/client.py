from typing import Optional, Callable, Awaitable
from contextlib import asynccontextmanager

from .manager import ConnectionManager
from .pool import Pool
from .protocols import Connection


class SQLiteConnectionPool:
    """
    A robust, high-performance asynchronous connection pool for SQLite.
    This class is the main user-facing entry point.
    """

    def __init__(
        self,
        connection_factory: Callable[[], Awaitable[Connection]],
        pool_size: int = 10,
        timeout: float = 30.0,
        max_connection_age: float = 3600.0,
        max_idle_time: float = 600.0,
    ):
        manager = ConnectionManager(connection_factory)
        self._pool = Pool(
            manager=manager,
            pool_size=pool_size,
            timeout=timeout,
            max_connection_age=max_connection_age,
            max_idle_time=max_idle_time,
        )

    @asynccontextmanager
    async def connection(self):
        """
        Acquire a connection from the pool.
        This is the primary way to interact with the pool.
        """
        conn = await self._pool.acquire()
        try:
            yield conn.raw_connection
        finally:
            self._pool.release(conn)

    @asynccontextmanager
    async def transaction(self):
        """
        A context manager for safe, atomic transactions.
        Automatically handles BEGIN, COMMIT, and ROLLBACK.
        """
        conn = await self._pool.acquire()
        try:
            await conn.raw_connection.execute("BEGIN")
            yield conn.raw_connection
            await conn.raw_connection.commit()
        except Exception:
            await conn.raw_connection.rollback()
            raise
        finally:
            self._pool.release(conn)

    async def close(self):
        """Close the pool and all its connections."""
        await self._pool.close()
