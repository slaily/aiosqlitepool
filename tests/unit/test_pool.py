import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from aiosqlitepool.pool import Pool
from aiosqlitepool.connection import PoolConnection


class FakeConnection:
    """A test-friendly implementation of the Connection protocol."""

    def __init__(self, should_fail: bool = False):
        self._connection = object()
        self.should_fail = should_fail
        self.closed = False
        self.rolled_back = False
        self.executions = 0

    async def execute(self, *args, **kwargs):
        self.executions += 1
        if self.should_fail:
            raise Exception("Simulated connection failure")
        await asyncio.sleep(0)

    async def rollback(self) -> None:
        if self.should_fail:
            raise Exception("Simulated connection failure")
        self.rolled_back = True
        await asyncio.sleep(0)

    async def close(self) -> None:
        if self.should_fail:
            raise Exception("Simulated connection failure")
        self.closed = True
        self._connection = None
        await asyncio.sleep(0)


class TestPool(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.connection_counter = 0

        async def connection_factory():
            self.connection_counter += 1
            return FakeConnection()

        self.connection_factory = connection_factory

    async def test_acquire_creates_new_connection(self):
        """Test acquire() creates a connection when the pool is empty."""
        pool = Pool(connection_factory=self.connection_factory, pool_size=1)
        self.addAsyncCleanup(pool.close)

        conn = await pool.acquire()

        self.assertIsInstance(conn, PoolConnection)
        self.assertEqual(self.connection_counter, 1)

    async def test_acquire_reuses_connection(self):
        """Test acquire() reuses a connection from the queue instead of creating a new one."""
        pool = Pool(connection_factory=self.connection_factory, pool_size=1)
        self.addAsyncCleanup(pool.close)

        # Acquire and release a connection to put it in the queue
        conn1 = await pool.acquire()
        await pool.release(conn1)

        # Reset the counter to track new creations
        initial_count = self.connection_counter

        conn2 = await pool.acquire()

        # Should be the same connection, no new creation
        self.assertEqual(self.connection_counter, initial_count)
        self.assertEqual(conn1.id, conn2.id)

    async def test_pool_size_limit(self):
        """Test that the pool waits when the size limit is reached."""
        pool = Pool(
            connection_factory=self.connection_factory, pool_size=1, timeout=0.01
        )
        self.addAsyncCleanup(pool.close)

        # Acquire the only connection
        await pool.acquire()

        # Only one connection should have been created
        self.assertEqual(self.connection_counter, 1)

        # Try to acquire another one, expecting a timeout because the pool is full
        with self.assertRaises(RuntimeError):
            await pool.acquire()

        # Ensure no new connection was created
        self.assertEqual(self.connection_counter, 1)

    async def test_pool_timeout(self):
        """Test that acquire() raises TimeoutError if no connection is available in time."""
        pool = Pool(
            connection_factory=self.connection_factory, pool_size=1, timeout=0.01
        )
        self.addAsyncCleanup(pool.close)

        # Acquire the only connection to fill the pool
        await pool.acquire()

        start_time = asyncio.get_event_loop().time()
        with self.assertRaises(RuntimeError):
            await pool.acquire()
        end_time = asyncio.get_event_loop().time()

        self.assertGreaterEqual(end_time - start_time, 0.01)

    async def test_release_returns_connection(self):
        """Test that release() puts a connection back in the queue."""
        pool = Pool(connection_factory=self.connection_factory, pool_size=1)
        self.addAsyncCleanup(pool.close)
        self.assertEqual(pool.size, 0)

        conn = await pool.acquire()
        self.assertEqual(pool.size, 0)  # It's checked out, not in the queue

        await pool.release(conn)
        self.assertEqual(pool.size, 1)  # Now it's back in the queue

    async def test_close_disposes_connections(self):
        """Test that close() properly closes all connections."""
        pool = Pool(connection_factory=self.connection_factory, pool_size=2)

        # Put two connections in the pool
        conn1 = await pool.acquire()
        conn2 = await pool.acquire()
        await pool.release(conn1)
        await pool.release(conn2)

        self.assertEqual(pool.size, 2)

        await pool.close()

        self.assertEqual(pool.size, 0)
        # Verify connections were closed
        self.assertTrue(conn1.raw_connection.closed)
        self.assertTrue(conn2.raw_connection.closed)

    async def test_failed_connection_factory_bubbles_up(self):
        """Test that connection factory failures bubble up to the user."""

        async def failing_factory():
            raise ValueError("Database not available")

        pool = Pool(connection_factory=failing_factory, pool_size=1)
        self.addAsyncCleanup(pool.close)

        with self.assertRaises(ValueError) as cm:
            await pool.acquire()

        self.assertEqual(str(cm.exception), "Database not available")

    async def test_connection_reset_failure_removes_connection(self):
        """Test that connections that fail to reset are removed from the pool."""

        async def factory():
            # Return a connection that will fail on rollback
            return FakeConnection(should_fail=True)

        pool = Pool(connection_factory=factory, pool_size=1)
        self.addAsyncCleanup(pool.close)

        conn = await pool.acquire()

        # Release should fail to reset and remove the connection
        await pool.release(conn)

        # Pool should be empty since the connection was removed
        self.assertEqual(pool.size, 0)
        self.assertEqual(pool.total_connections, 0)
