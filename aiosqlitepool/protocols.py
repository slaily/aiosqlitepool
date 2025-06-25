import sys
from typing import Any

if sys.version_info >= (3, 8):
    from typing import Protocol, runtime_checkable
else:
    from typing_extensions import Protocol, runtime_checkable


@runtime_checkable
class Connection(Protocol):
    async def execute(self, *args: Any, **kwargs: Any) -> Any: ...

    async def rollback(self) -> None: ...

    async def close(self) -> None: ...
