import unittest

from asyncio import sleep

from tests.helpers import StubConnection
from aiosqlitepool.connection import PoolConnection


class TestPoolConnection(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """Set up a default stub connection for tests."""
        self.raw_conn = StubConnection(connection_id=1)
        self.pool_conn = PoolConnection(connection=self.raw_conn)

    def test_initial_attributes(self):
        """Test that a PoolConnection is created with correct basic attributes."""
        self.assertIs(self.pool_conn.raw_connection, self.raw_conn)
        self.assertIsInstance(self.pool_conn.id, str)
        self.assertIsNone(self.pool_conn.idle_since)
        self.assertEqual(self.pool_conn.idle_time, 0.0)

    def test_mark_as_idle_and_in_use(self):
        """Test that idle status is marked correctly."""
        self.pool_conn.mark_as_idle()
        self.assertIsNotNone(self.pool_conn.idle_since)

        self.pool_conn.mark_as_in_use()
        self.assertIsNone(self.pool_conn.idle_since)
        self.assertEqual(self.pool_conn.idle_time, 0.0)

    async def test_idle_time_tracking(self):
        """Test that idle time is tracked correctly after being marked."""
        self.pool_conn.mark_as_idle()
        await sleep(0.02)
        self.assertGreater(self.pool_conn.idle_time, 0.01)

    async def test_create_class_method(self):
        """Test the factory classmethod for creating connections."""

        async def factory():
            return StubConnection(connection_id=2)

        pool_conn = await PoolConnection.create(factory)
        self.assertIsInstance(pool_conn, PoolConnection)
        self.assertIsInstance(pool_conn.raw_connection, StubConnection)
        self.assertEqual(pool_conn.raw_connection.connection_id, 2)

    async def test_is_alive_calls_execute(self):
        """Test that is_alive() calls execute on the raw connection."""
        await self.pool_conn.is_alive()
        self.assertEqual(self.raw_conn.execute_count, 1)

    async def test_is_alive_propagates_failure(self):
        """Test that is_alive() propagates exceptions from execute."""
        self.raw_conn._fail_on_execute = True
        with self.assertRaisesRegex(RuntimeError, "Failed to execute"):
            await self.pool_conn.is_alive()

    async def test_reset_calls_rollback(self):
        """Test that reset() calls rollback on the raw connection."""
        await self.pool_conn.reset()
        self.assertEqual(self.raw_conn.rollback_count, 1)

    async def test_reset_propagates_failure(self):
        """Test that reset() propagates exceptions from rollback."""
        self.raw_conn._fail_on_reset = True
        with self.assertRaisesRegex(RuntimeError, "Failed to reset connection"):
            await self.pool_conn.reset()

    async def test_close_calls_close(self):
        """Test that close() calls close on the raw connection."""
        await self.pool_conn.close()
        self.assertEqual(self.raw_conn.close_count, 1)
        self.assertTrue(self.raw_conn.is_closed)

    async def test_close_is_fault_tolerant(self):
        """Test that close() does not raise an error if the raw connection fails to close."""
        self.raw_conn._fail_on_close = True
        try:
            await self.pool_conn.close()
        except Exception:
            self.fail("PoolConnection.close() should not raise an exception.")


if __name__ == "__main__":
    unittest.main()
