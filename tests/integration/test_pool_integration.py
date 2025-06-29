import asyncio
import unittest
import os
import tempfile
import aiosqlite

from aiosqlitepool.client import SQLiteConnectionPool


class TestPoolIntegration(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Create a temporary database for each test."""
        # Create a temporary file
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.db_fd)  # Close the file descriptor

        # Define a connection factory for the tests
        self.connection_factory = lambda: aiosqlite.connect(self.db_path)

    async def asyncTearDown(self):
        """Remove the temporary database file after each test."""
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    async def test_transaction_state_is_reset(self):
        """Verify that returning a connection rolls back any open transaction."""
        async with SQLiteConnectionPool(self.connection_factory, pool_size=1) as pool:
            # Use a connection and leave it in an uncommitted transaction
            async with pool.connection() as conn:
                await conn.execute("CREATE TABLE test (id INTEGER, value TEXT)")
                await conn.execute("BEGIN")
                await conn.execute("INSERT INTO test VALUES (1, 'uncommitted')")

            # Get the same connection again (it should have been reset)
            async with pool.connection() as conn:
                # This would fail if the previous transaction was still active
                await conn.execute("BEGIN")
                await conn.execute("INSERT INTO test VALUES (2, 'committed')")
                await conn.commit()

                cursor = await conn.execute("SELECT * FROM test ORDER BY id")
                rows = await cursor.fetchall()
                self.assertEqual(len(rows), 1, "Should only have committed data")
                self.assertEqual(
                    rows[0],
                    (2, "committed"),
                    "Uncommitted data should have been rolled back",
                )

    async def test_pool_recovers_from_closed_connection(self):
        """Verify the pool detects and replaces a connection that was closed externally."""
        async with SQLiteConnectionPool(self.connection_factory, pool_size=1) as pool:
            # Acquire a connection and "corrupt" it by closing it
            async with pool.connection() as conn:
                await conn.close()

            # The pool should handle the bad connection and give us a new, working one
            async with pool.connection() as conn:
                # This command would fail if the connection wasn't replaced
                cursor = await conn.execute("SELECT 1")
                result = await cursor.fetchone()
                self.assertEqual(result, (1,))

    async def test_concurrent_access_is_safe(self):
        """Verify the pool handles many concurrent workers safely."""
        async with SQLiteConnectionPool(self.connection_factory, pool_size=3) as pool:

            async def worker(worker_id: int):
                for i in range(3):
                    async with pool.connection() as conn:
                        await conn.execute(
                            "CREATE TABLE IF NOT EXISTS concurrent (worker_id INTEGER, iteration INTEGER)"
                        )
                        await conn.execute("BEGIN")
                        await conn.execute(
                            "INSERT INTO concurrent VALUES (?, ?)", (worker_id, i)
                        )
                        await asyncio.sleep(0.01)  # Simulate work
                        if i % 2 == 0:
                            await conn.commit()
                return True

            tasks = [worker(i) for i in range(5)]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            self.assertTrue(
                all(res is True for res in results),
                "All workers should complete successfully",
            )

            # Verify final state: 5 workers, each committing iterations 0 and 2
            async with pool.connection() as conn:
                cursor = await conn.execute("SELECT COUNT(*) FROM concurrent")
                count = (await cursor.fetchone())[0]
                self.assertEqual(
                    count, 5 * 2, "Incorrect number of committed transactions found"
                )

    async def test_acquisition_times_out_when_pool_is_full(self):
        """Verify acquire() raises a timeout error when the pool is exhausted."""
        # Use a client with a very small timeout for speed
        pool = SQLiteConnectionPool(
            self.connection_factory, pool_size=1, acquisition_timeout=0.1
        )

        # Hold the only available connection
        async with pool.connection():
            # This second acquisition attempt should time out
            with self.assertRaises(asyncio.TimeoutError):
                async with pool.connection():
                    # We should never get here
                    self.fail("Acquired a connection when pool should have timed out.")

        await pool.close()
