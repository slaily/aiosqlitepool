import asyncio
from typing import Any, Optional


class StubConnection:
    """A stub connection that conforms to the Connection protocol."""

    def __init__(
        self,
        connection_id: int,
        fail_on_execute: bool = False,
        fail_on_reset: bool = False,
        fail_on_close: bool = False,
        delay_on_close: Optional[float] = None,
    ):
        self.connection_id = connection_id
        self._fail_on_execute = fail_on_execute
        self._fail_on_reset = fail_on_reset
        self._fail_on_close = fail_on_close
        self._delay_on_close = delay_on_close

        self.is_closed = False
        self.rollback_count = 0
        self.execute_count = 0
        self.close_count = 0

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        if self.is_closed:
            raise IOError("Connection is closed")
        if self._fail_on_execute:
            raise RuntimeError("Failed to execute")
        self.execute_count += 1
        return "ok"

    async def rollback(self) -> None:
        if self.is_closed:
            raise IOError("Connection is closed")
        if self._fail_on_reset:
            raise RuntimeError("Failed to reset connection")
        self.rollback_count += 1

    async def close(self) -> None:
        if self._fail_on_close:
            raise RuntimeError("Failed to close connection")
        if self._delay_on_close:
            await asyncio.sleep(self._delay_on_close)
        self.is_closed = True
        self.close_count += 1


class ConnectionFactory:
    """A factory to create stub connections for testing."""

    def __init__(
        self,
        fail_on_execute: bool = False,
        fail_on_reset: bool = False,
        fail_on_close: bool = False,
        delay_on_close: Optional[float] = None,
    ):
        self._fail_on_execute = fail_on_execute
        self._fail_on_reset = fail_on_reset
        self._fail_on_close = fail_on_close
        self._delay_on_close = delay_on_close
        self.created_connections = 0

    async def __call__(self) -> StubConnection:
        self.created_connections += 1
        return StubConnection(
            connection_id=self.created_connections,
            fail_on_execute=self._fail_on_execute,
            fail_on_reset=self._fail_on_reset,
            fail_on_close=self._fail_on_close,
            delay_on_close=self._delay_on_close,
        )
