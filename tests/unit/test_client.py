import unittest

from aiosqlitepool.client import SQLiteConnectionPool
from aiosqlitepool.pool import Pool
from tests.helpers import ConnectionFactory


class TestClient(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """Set up a default connection factory for tests."""
        self.factory = ConnectionFactory()

    async def test_init_creates_pool(self):
        """Test that the client initializes an underlying pool."""
        async with SQLiteConnectionPool(connection_factory=self.factory) as pool_client:
            self.assertIsInstance(pool_client._pool, Pool)

    async def test_connection_context_manager_acquires_and_releases(self):
        """Test that the connection context manager acquires and releases a connection."""
        async with SQLiteConnectionPool(
            connection_factory=self.factory, pool_size=1
        ) as pool_client:
            self.assertEqual(pool_client._pool.size, 0)
            self.assertEqual(len(pool_client._pool._connection_registry), 0)

            async with pool_client.connection() as conn:
                self.assertIsNotNone(conn)
                self.assertEqual(pool_client._pool.size, 0)
                self.assertEqual(len(pool_client._pool._connection_registry), 1)

                # Check that the yielded connection is the raw one
                self.assertNotIsInstance(conn, PoolConnection)
                self.assertEqual(conn.connection_id, 1)

            self.assertEqual(pool_client._pool.size, 1)
            self.assertEqual(len(pool_client._pool._connection_registry), 1)

    async def test_connection_context_manager_releases_on_exception(self):
        """Test that the context manager releases the connection even if an exception occurs."""
        async with SQLiteConnectionPool(
            connection_factory=self.factory, pool_size=1
        ) as pool_client:
            self.assertEqual(pool_client._pool.size, 0)

            with self.assertRaisesRegex(ValueError, "Simulating an error"):
                async with pool_client.connection():
                    self.assertEqual(pool_client._pool.size, 0)
                    raise ValueError("Simulating an error")

            self.assertEqual(
                pool_client._pool.size,
                1,
                "Connection was not released back to the pool",
            )

    async def test_close_closes_underlying_pool(self):
        """Test that closing the client closes the underlying pool."""
        pool_client = SQLiteConnectionPool(connection_factory=self.factory, pool_size=1)

        async with pool_client.connection():
            pass

        self.assertFalse(pool_client._pool.is_closed)
        await pool_client.close()
        self.assertTrue(pool_client._pool.is_closed)

    async def test_client_is_async_context_manager(self):
        """Test that the client can be used as an async context manager for cleanup."""
        pool_client = SQLiteConnectionPool(connection_factory=self.factory)
        async with pool_client:
            self.assertFalse(pool_client._pool.is_closed)
        self.assertTrue(pool_client._pool.is_closed)


# To test PoolConnection which is not directly exposed but used in client
from aiosqlitepool.connection import PoolConnection

if __name__ == "__main__":
    unittest.main()
