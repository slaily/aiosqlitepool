import asyncio
import unittest
import os
import tempfile
import aiosqlite

from aiosqlitepool.client import SQLiteConnectionPool


class TestPoolWorkload(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        """Create a temporary database for each test."""
        self.db_fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.db_fd)
        self.connection_factory = lambda: aiosqlite.connect(self.db_path)

    async def asyncTearDown(self):
        """Remove the temporary database file after each test."""
        if os.path.exists(self.db_path):
            os.remove(self.db_path)

    async def test_realistic_concurrent_workload(self):
        """
        Tests the pool under a realistic, high-throughput simulation with mixed read/write operations.
        """
        # 1. Set up a realistic database schema and data
        async with aiosqlite.connect(self.db_path) as conn:
            await conn.executescript("""
                CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);
                CREATE TABLE posts (id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, content TEXT);
            """)
            await conn.execute("INSERT INTO users (id, name) VALUES (1, 'test_user')")
            await conn.commit()

        # 2. Define a workload simulator
        class Workload:
            async def read_op(self, conn):
                cursor = await conn.execute("SELECT name FROM users WHERE id = 1")
                await cursor.fetchone()
                return True

            async def write_op(self, conn):
                post_id = os.urandom(4).hex()
                await conn.execute(
                    "INSERT INTO posts (user_id, title, content) VALUES (?, ?, ?)",
                    (1, f"Post {post_id}", "content"),
                )
                return True

        workload = Workload()
        num_workers = 10
        ops_per_worker = 20
        total_writes = 0

        write_lock = asyncio.Lock()

        async def safe_write_op(conn):
            nonlocal total_writes
            result = await workload.write_op(conn)
            async with write_lock:
                total_writes += 1
            return result

        # 3. Define the concurrent worker
        async def worker(pool):
            writes_done = 0
            for _ in range(ops_per_worker):
                async with pool.connection() as conn:
                    if __import__("random").random() < 0.8:
                        await workload.read_op(conn)
                    else:
                        await safe_write_op(conn)
                        writes_done += 1
            return writes_done

        # 4. Run the simulation
        async with SQLiteConnectionPool(self.connection_factory, pool_size=5) as pool:
            tasks = [worker(pool) for _ in range(num_workers)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 5. Assert correctness
            for res in results:
                self.assertNotIsInstance(
                    res, Exception, f"Worker failed with exception: {res}"
                )

            async with pool.connection() as conn:
                cursor = await conn.execute("SELECT COUNT(*) FROM posts")
                final_post_count = (await cursor.fetchone())[0]

                total_writes_from_workers = sum(
                    res for res in results if isinstance(res, int)
                )

                self.assertEqual(final_post_count, total_writes)
                self.assertEqual(total_writes_from_workers, total_writes)
