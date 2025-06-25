import unittest
from typing import Any
import inspect

from aiosqlitepool.protocols import Connection


# --- Fake Implementations for Testing ---


class GoodConnection:
    """
    A class that correctly implements the Connection protocol.
    This is our success case.
    """

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        return "Success"

    async def rollback(self) -> None:
        pass

    async def close(self) -> None:
        self.closed = True
        pass


class FailingConnection:
    """
    An implementation that raises an exception from a method.
    This is our runtime error case.
    """

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        raise ValueError("Execution failed")

    async def rollback(self) -> None:
        pass

    async def close(self) -> None:
        pass


class IncompleteConnection:
    """
    An implementation that is missing the `close` method.
    This is our structural error case.
    """

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        return "Partial Success"

    async def rollback(self) -> None:
        pass


class SyncConnection:
    """
    An implementation where methods are not async.
    This is another structural error case.
    """

    def execute(self, *args: Any, **kwargs: Any) -> Any:
        return "Not async"

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


# --- Tests ---


class TestConnectionProtocol(unittest.IsolatedAsyncioTestCase):
    async def test_good_connection_methods(self):
        """
        Success Case: A compliant object can be used as expected.
        We can call its methods and get expected results.
        """
        conn = GoodConnection()
        result = await conn.execute("SELECT 1")
        self.assertEqual(result, "Success")
        await conn.rollback()
        await conn.close()
        self.assertTrue(hasattr(conn, "closed") and conn.closed)

    async def test_failing_connection_raises_error(self):
        """
        Error Case (Runtime): Calling a method on a compliant object
        that fails internally should raise the expected exception.
        """
        conn = FailingConnection()
        with self.assertRaisesRegex(ValueError, "Execution failed"):
            await conn.execute("SELECT 1")

    def test_incomplete_connection_raises_error(self):
        """
        Error Case (Structural): An object missing a method will raise
        an AttributeError when that method is called. This is the essence
        of duck typing.
        """
        conn = IncompleteConnection()
        with self.assertRaises(AttributeError):
            # This line will fail because conn.close doesn't exist.
            conn.close()

    async def test_sync_connection_raises_error(self):
        """
        Error Case (Structural): A class with sync methods is not a valid
        implementation because its methods are not awaitable, which will
        raise a TypeError at the call site.
        """
        conn = SyncConnection()

        # A more robust check is to inspect the methods directly.
        self.assertFalse(inspect.iscoroutinefunction(conn.execute))

        # Attempting to await a non-awaitable method will raise a TypeError.
        with self.assertRaises(TypeError):
            await conn.execute()


if __name__ == "__main__":
    unittest.main()
