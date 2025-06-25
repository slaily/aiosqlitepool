from typing import (
    Any,
    Protocol,
    runtime_checkable,
)


@runtime_checkable
class Connection(Protocol):
    async def execute(self, *args: Any, **kwargs: Any) -> Any: ...

    async def rollback(self) -> None: ...

    async def close(self) -> None: ...
