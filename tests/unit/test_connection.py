import time
import unittest

from typing import Any
from asyncio import sleep

from aiosqlitepool.connection import PoolConnection


class SQLiteConnection:
    def __init__(self, should_fail: bool = False):
        self._connection = object()
        self.closed = False
        self.rolled_back = False
        self.should_fail = should_fail
        self.executions = 0

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        self.executions += 1
        if self.should_fail:
            raise Exception("Simulated connection failure")
        await sleep(0)

    async def rollback(self) -> None:
        if self.should_fail:
            raise Exception("Simulated connection failure")
        self.rolled_back = True
        await sleep(0)

    async def close(self) -> None:
        if self.should_fail:
            raise Exception("Simulated connection failure")
        self.closed = True
        self._connection = None
        await sleep(0)


class TestPoolConnection(unittest.IsolatedAsyncioTestCase):
    async def test_creation_and_basic_attributes(self):
        """Test that a PoolConnection is created with correct basic attributes."""
        mock_conn = SQLiteConnection()
        pool_conn = PoolConnection(connection=mock_conn)

        self.assertIs(pool_conn.raw_connection, mock_conn)
        self.assertIsInstance(pool_conn.id, str)
        self.assertTrue(len(pool_conn.id) > 0)
        self.assertIsNone(pool_conn.idle_since)
        self.assertEqual(pool_conn.idle_time, 0.0)

    async def test_idle_time_tracking(self):
        """Test that idle time is tracked correctly."""
        mock_conn = SQLiteConnection()
        pool_conn = PoolConnection(connection=mock_conn)

        # Initially not idle
        self.assertEqual(pool_conn.idle_time, 0.0)

        # Mark as idle
        pool_conn.mark_as_idle()
        self.assertIsNotNone(pool_conn.idle_since)

        # Wait and check idle time
        await sleep(0.1)
        self.assertGreater(pool_conn.idle_time, 0.09)
        self.assertLess(pool_conn.idle_time, 0.15)

        # Mark as in use again
        pool_conn.mark_as_in_use()
        self.assertIsNone(pool_conn.idle_since)
        self.assertEqual(pool_conn.idle_time, 0.0)

    async def test_factory_method_creates_connection(self):
        """Test that the factory method creates connections properly."""

        async def connection_factory():
            return SQLiteConnection()

        pool_conn = await PoolConnection.create(connection_factory)

        self.assertIsInstance(pool_conn, PoolConnection)
        self.assertIsInstance(pool_conn.raw_connection, SQLiteConnection)
        self.assertIsInstance(pool_conn.id, str)
        self.assertTrue(len(pool_conn.id) > 0)

    async def test_is_alive_healthy_connection(self):
        """Test health check on a healthy connection."""
        mock_conn = SQLiteConnection()
        pool_conn = PoolConnection(connection=mock_conn)

        result = await pool_conn.is_alive()

        self.assertTrue(result)
        self.assertEqual(mock_conn.executions, 1)

    async def test_is_alive_failing_connection(self):
        """Test health check on a failing connection."""
        mock_conn = SQLiteConnection(should_fail=True)
        pool_conn = PoolConnection(connection=mock_conn)

        with self.assertRaises(Exception):
            await pool_conn.is_alive()

    async def test_reset_successful(self):
        """Test successful connection reset."""
        mock_conn = SQLiteConnection()
        pool_conn = PoolConnection(connection=mock_conn)

        result = await pool_conn.reset()

        self.assertTrue(result)
        self.assertTrue(mock_conn.rolled_back)

    async def test_reset_failing(self):
        """Test connection reset failure."""
        mock_conn = SQLiteConnection(should_fail=True)
        pool_conn = PoolConnection(connection=mock_conn)

        with self.assertRaises(Exception):
            await pool_conn.reset()

    async def test_close_successful(self):
        """Test successful connection close."""
        mock_conn = SQLiteConnection()
        pool_conn = PoolConnection(connection=mock_conn)

        await pool_conn.close()

        self.assertTrue(mock_conn.closed)

    async def test_close_failing_doesnt_raise(self):
        """Test that connection close failure doesn't raise exceptions."""
        mock_conn = SQLiteConnection(should_fail=True)
        pool_conn = PoolConnection(connection=mock_conn)

        # Should not raise despite mock_conn.close() failing
        await pool_conn.close()
