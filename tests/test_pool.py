import unittest
import asyncio
from unittest.mock import AsyncMock, MagicMock

from aiosqlitepool.manager import ConnectionManager
from aiosqlitepool.pool import Pool
from aiosqlitepool.connection import PoolConnection


class TestPool(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_manager = AsyncMock(spec=ConnectionManager)
        # Configure the mock connection that the manager will "create"
        mock_conn = MagicMock(spec=PoolConnection)
        mock_conn.id = "conn_1"
        mock_conn.age = 0  # Assume it's a new connection

        self.mock_manager.create.return_value = mock_conn

    async def test_acquire_creates_new_connection(self):
        """
        Test acquire() creates a connection via the manager when the pool is empty.
        """
        pool = Pool(manager=self.mock_manager, pool_size=1)
        self.addAsyncCleanup(pool.close)

        conn = await pool.acquire()

        self.mock_manager.create.assert_awaited_once()
        self.assertIsInstance(conn, PoolConnection)

    async def test_acquire_reuses_connection(self):
        """
        Test acquire() reuses a connection from the queue instead of creating a new one.
        """
        pool = Pool(manager=self.mock_manager, pool_size=1)
        self.addAsyncCleanup(pool.close)

        # Acquire and release a connection to put it in the queue
        conn1 = await pool.acquire()
        pool.release(conn1)

        # Reset the mock to ensure create() isn't called again
        self.mock_manager.create.reset_mock()

        conn2 = await pool.acquire()

        self.mock_manager.create.assert_not_awaited()
        self.assertIs(conn1, conn2)

    async def test_pool_size_limit(self):
        """
        Test that the pool waits when the size limit is reached.
        """
        pool = Pool(manager=self.mock_manager, pool_size=1, timeout=0.01)
        self.addAsyncCleanup(pool.close)

        # Acquire the only connection
        await pool.acquire()

        # The manager should only have been called once
        self.mock_manager.create.assert_awaited_once()

        # Try to acquire another one, expecting a timeout because the pool is full
        with self.assertRaises(asyncio.TimeoutError):
            await pool.acquire()

        # Ensure no new connection was created
        self.mock_manager.create.assert_awaited_once()

    async def test_pool_timeout(self):
        """
        Test that acquire() raises TimeoutError if no connection is available in time.
        """
        pool = Pool(manager=self.mock_manager, pool_size=1, timeout=0.01)
        self.addAsyncCleanup(pool.close)

        # Acquire the only connection to fill the pool
        await pool.acquire()

        start_time = asyncio.get_event_loop().time()
        with self.assertRaises(asyncio.TimeoutError):
            await pool.acquire()
        end_time = asyncio.get_event_loop().time()

        self.assertGreaterEqual(end_time - start_time, 0.01)

    async def test_release_returns_connection(self):
        """
        Test that release() puts a connection back in the queue.
        """
        pool = Pool(manager=self.mock_manager, pool_size=1)
        self.addAsyncCleanup(pool.close)
        self.assertEqual(pool.size, 0)

        conn = await pool.acquire()
        self.assertEqual(pool.size, 0)  # It's checked out, not in the queue

        pool.release(conn)
        self.assertEqual(pool.size, 1)  # Now it's back in the queue

    async def test_close_disposes_connections(self):
        """
        Test that close() uses the manager to close all available connections.
        """
        pool = Pool(manager=self.mock_manager, pool_size=2)

        # Put two connections in the pool
        conn1 = await pool.acquire()
        conn2 = await pool.acquire()
        pool.release(conn1)
        pool.release(conn2)

        self.assertEqual(pool.size, 2)

        await pool.close()

        self.assertEqual(pool.size, 0)
        # Check that the manager was called to close both connections
        self.mock_manager.close.assert_any_await(conn1)
        self.mock_manager.close.assert_any_await(conn2)
        self.assertEqual(self.mock_manager.close.await_count, 2)
