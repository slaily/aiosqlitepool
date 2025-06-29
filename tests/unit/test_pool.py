import asyncio
import unittest

from aiosqlitepool.pool import Pool
from aiosqlitepool.exceptions import PoolClosedError, PoolConnectionAcquireTimeoutError

from tests.helpers import ConnectionFactory


class TestPool(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Set up a default factory and pool for tests."""
        self.factory = ConnectionFactory()
        self.pool = Pool(connection_factory=self.factory, pool_size=5)

    async def asyncTearDown(self):
        """Ensure the pool is closed after each test."""
        if self.pool and not self.pool.is_closed:
            await self.pool.close()

    async def test_acquire_from_new_pool(self):
        """Test acquiring a connection from a fresh pool."""
        self.assertEqual(self.pool.size, 0, "New pool queue should be empty")

        conn = await self.pool.acquire()

        self.assertIsNotNone(conn, "Acquired connection should not be None")
        self.assertEqual(
            len(self.pool._connection_registry),
            1,
            "Registry should have one connection",
        )
        self.assertEqual(self.pool.size, 0, "Queue should be empty after acquiring")

        await self.pool.release(conn)

    async def test_release_returns_connection_to_queue(self):
        """Test that releasing a connection adds it to the idle queue."""
        conn = await self.pool.acquire()
        await self.pool.release(conn)

        self.assertEqual(
            self.pool.size, 1, "Queue should have one connection after release"
        )
        self.assertEqual(
            len(self.pool._connection_registry),
            1,
            "Registry should still have one connection",
        )

    async def test_pool_exhaustion_and_timeout(self):
        """Test that acquiring from a full pool times out."""
        # This test requires a specific configuration, so we create a new pool.
        self.pool = Pool(
            connection_factory=self.factory, pool_size=1, acquisition_timeout=0.1
        )

        conn1 = await self.pool.acquire()

        with self.assertRaises(PoolConnectionAcquireTimeoutError):
            await self.pool.acquire()

        await self.pool.release(conn1)

    async def test_pool_close_under_load(self):
        """Test the pool closes gracefully while workers are active."""

        async def worker():
            for _ in range(5):
                try:
                    conn = await self.pool.acquire()
                    await asyncio.sleep(0.01)
                    await self.pool.release(conn)
                except (PoolClosedError, asyncio.CancelledError):
                    break

        workers = [asyncio.create_task(worker()) for _ in range(10)]

        await asyncio.sleep(0.05)
        await self.pool.close()

        with self.assertRaises(PoolClosedError):
            await self.pool.acquire()

        await asyncio.gather(*workers)
        self.assertTrue(self.pool.is_closed)

    async def test_connection_retirement_on_reset_failure(self):
        """Test that a connection failing on reset is retired."""
        # Needs a factory that produces failing connections.
        self.factory = ConnectionFactory(fail_on_reset=True)
        self.pool = Pool(connection_factory=self.factory, pool_size=2)

        conn = await self.pool.acquire()
        self.assertEqual(len(self.pool._connection_registry), 1)

        await self.pool.release(conn)

        self.assertEqual(
            len(self.pool._connection_registry),
            0,
            "Faulty connection should be removed from registry",
        )
        self.assertEqual(self.pool.size, 0, "Faulty connection should not be in queue")
        self.assertTrue(
            conn.raw_connection.is_closed, "Underlying connection should be closed"
        )

    async def test_idle_timeout_enforcement(self):
        """Test that idle connections are retired after the timeout."""
        self.pool = Pool(connection_factory=self.factory, pool_size=1, idle_timeout=0.1)

        conn = await self.pool.acquire()
        await self.pool.release(conn)

        await asyncio.sleep(0.2)

        new_conn = await self.pool.acquire()

        self.assertNotEqual(
            conn.id, new_conn.id, "A new connection should have been created"
        )
        self.assertEqual(
            self.factory.created_connections, 2, "Factory should have been called twice"
        )
        self.assertTrue(
            conn.raw_connection.is_closed, "Stale connection should be closed"
        )

        await self.pool.release(new_conn)

    async def test_release_to_closed_pool(self):
        """Test that releasing a connection to a closed pool closes it."""
        conn = await self.pool.acquire()

        await self.pool.close()

        await self.pool.release(conn)

        self.assertTrue(conn.raw_connection.is_closed)
        self.assertEqual(len(self.pool._connection_registry), 0)

    async def test_acquire_from_closed_pool_fails(self):
        """Test that acquiring from a closed pool raises an error."""
        await self.pool.close()

        with self.assertRaises(PoolClosedError):
            await self.pool.acquire()


if __name__ == "__main__":
    unittest.main()
