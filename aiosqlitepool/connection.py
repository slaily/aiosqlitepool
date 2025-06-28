import time

from uuid import uuid4
from typing import Optional, Callable, Awaitable

from .protocols import Connection


class PoolConnection:
    def __init__(self, connection: Connection):
        self._id = str(uuid4())
        self.raw_connection = connection
        self.idle_since: Optional[float] = None

    @classmethod
    async def create(
        cls, connection_factory: Callable[[], Awaitable[Connection]]
    ) -> "PoolConnection":
        raw_conn = await connection_factory()
        return cls(connection=raw_conn)

    @property
    def id(self) -> str:
        return self._id

    def mark_as_in_use(self) -> None:
        self.idle_since = None

        return None

    def mark_as_idle(self) -> None:
        self.idle_since = time.time()

        return None

    @property
    def idle_time(self) -> float:
        if self.idle_since is None:
            return 0.0

        return time.time() - self.idle_since

    async def is_alive(self) -> bool:
        await self.raw_connection.execute("SELECT 1")

        return True

    async def reset(self) -> None:
        await self.raw_connection.rollback()

        return None

    async def close(self) -> None:
        try:
            await self.raw_connection.close()
        except Exception:
            # This method must be fault-tolerant. A failure to close a connection,
            # which may already be broken, should not disrupt Pool's cleanup operations.
            return None
