import time

from typing import Optional, Callable, Awaitable

from .protocols import Connection


class PoolConnection:
    _connection_counter = 0

    def __init__(self, connection: Connection, connection_id: str):
        self.raw_connection = connection
        self.id = connection_id
        self.idle_since: Optional[float] = None

    @classmethod
    async def create(
        cls, connection_factory: Callable[[], Awaitable[Connection]]
    ) -> "PoolConnection":
        cls._connection_counter += 1
        connection_id = f"conn_{cls._connection_counter}"
        raw_conn = await connection_factory()
        return cls(connection=raw_conn, connection_id=connection_id)

    def mark_as_in_use(self) -> None:
        self.idle_since = None

    def mark_as_idle(self) -> None:
        self.idle_since = time.time()

    @property
    def idle_time(self) -> float:
        if self.idle_since is None:
            return 0.0
        return time.time() - self.idle_since

    async def is_alive(self) -> bool:
        await self.raw_connection.execute("SELECT 1")
        return True

    async def reset(self) -> bool:
        await self.raw_connection.rollback()
        return True

    async def close(self) -> None:
        try:
            await self.raw_connection.close()
        except Exception:
            # We don't want close() to fail, as it's often a cleanup call.
            # The previous is_alive/reset check should have already caught the error.
            pass
