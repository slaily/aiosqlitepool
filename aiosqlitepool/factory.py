import aiosqlite
from typing import Optional, Callable, Awaitable


class ConnectionFactory:
    """
    A factory responsible for creating raw aiosqlite connections, either from
    a database path or a custom factory function.
    """

    def __init__(
        self,
        database_path: Optional[str] = None,
        conn_factory: Optional[Callable[[], Awaitable[aiosqlite.Connection]]] = None,
    ):
        if not database_path and not conn_factory:
            raise ValueError(
                "Either 'database_path' or 'conn_factory' must be provided."
            )
        self._database_path = database_path
        self._conn_factory = conn_factory

    async def create(self) -> aiosqlite.Connection:
        """Create and return a new aiosqlite connection."""
        try:
            if self._conn_factory:
                return await self._conn_factory()
            elif self._database_path:
                return await aiosqlite.connect(self._database_path)
            else:
                # This line is technically unreachable due to the __init__ check,
                # but it's good practice for ensuring type safety and clarity.
                raise RuntimeError("No connection source configured.")
        except Exception as e:
            # In a real-world scenario, you might add more specific logging
            # or error handling here.
            raise
