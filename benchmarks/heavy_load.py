"""
aiosqlitepool heavy load benchmark

Usage:
    python benchmarks/heavy_load.py                    # Default: 10k requests 100 workers 5 pool size 1000 users 10 posts per user
    python benchmarks/heavy_load.py --requests 100000 --workers 200 --pool-size 50 --users 5000
    python benchmarks/heavy_load.py --help             # All options
"""

import os
import time
import random
import asyncio
import argparse

from typing import List
from dataclasses import dataclass

import aiosqlite
import numpy as np

from aiosqlitepool import SQLiteConnectionPool


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class BenchmarkConfig:
    """Simple benchmark configuration."""

    total_requests: int = 10_000
    concurrent_workers: int = 100
    pool_size: int = 5
    users: int = 1_000
    posts_per_user: int = 10

    @property
    def requests_per_worker(self) -> int:
        return self.total_requests // self.concurrent_workers

    @property
    def total_posts(self) -> int:
        return self.users * self.posts_per_user


# =============================================================================
# Database Setup
# =============================================================================

# Query designed to stress connection overhead with JOINs and aggregations
BENCHMARK_QUERY = """
    SELECT 
        p.id,
        p.title, 
        u.name as author,
        COUNT(c.id) as comment_count,
        COUNT(l.id) as like_count
    FROM posts p
    JOIN users u ON p.user_id = u.id
    LEFT JOIN comments c ON c.post_id = p.id  
    LEFT JOIN likes l ON l.post_id = p.id
    WHERE p.id = ?
    GROUP BY p.id, p.title, u.name;
"""


async def create_sqlite_connection(database_path: str) -> aiosqlite.Connection:
    """Create optimized SQLite connection with performance settings."""
    connection = await aiosqlite.connect(database_path)
    await connection.execute("PRAGMA journal_mode = WAL")
    await connection.execute("PRAGMA synchronous = NORMAL")
    await connection.execute("PRAGMA cache_size = 10000")

    return connection


async def create_benchmark_database(
    database_path: str, config: BenchmarkConfig
) -> None:
    """Create realistic benchmark database with heavy load simulation."""
    comments_per_post = 50
    likes_per_post = 100
    total_comments = config.total_posts * comments_per_post
    total_likes = config.total_posts * likes_per_post
    db_connection = await create_sqlite_connection(database_path)
    await _create_database_tables(db_connection)
    await _create_database_indexes(db_connection)
    await _insert_users(db_connection, config.users)
    await _insert_posts(db_connection, config)
    await _insert_comments(db_connection, total_comments, config)
    await _insert_likes(db_connection, total_likes, config)

    return None


async def _create_database_tables(database: aiosqlite.Connection) -> None:
    """Create all required database tables."""
    table_schemas = [
        """CREATE TABLE users (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL
        )""",
        """CREATE TABLE posts (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )""",
        """CREATE TABLE comments (
            id INTEGER PRIMARY KEY,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            FOREIGN KEY (post_id) REFERENCES posts(id)
        )""",
        """CREATE TABLE likes (
            id INTEGER PRIMARY KEY,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            UNIQUE(post_id, user_id)
        )""",
    ]

    for schema in table_schemas:
        await database.execute(schema)


async def _insert_users(database: aiosqlite.Connection, user_count: int) -> None:
    """Insert all users into database."""
    users = [(i, f"User {i}") for i in range(1, user_count + 1)]
    await database.executemany("INSERT INTO users (id, name) VALUES (?, ?)", users)
    await database.commit()

    return None


async def _insert_posts(
    db_connection: aiosqlite.Connection, config: BenchmarkConfig
) -> None:
    """Insert all posts into database."""
    posts = []
    post_id = 1
    for user_id in range(1, config.users + 1):
        for i in range(config.posts_per_user):
            posts.append((post_id, user_id, f"Post {i} by User {user_id}"))
            post_id += 1

    await db_connection.executemany(
        "INSERT INTO posts (id, user_id, title) VALUES (?, ?, ?)", posts
    )
    await db_connection.commit()

    return None


async def _insert_comments(
    db_connection: aiosqlite.Connection, total_comments: int, config: BenchmarkConfig
) -> None:
    """Insert comments in batches for performance."""
    batch_size = 10_000

    for batch_start in range(0, total_comments, batch_size):
        batch_end = min(batch_start + batch_size, total_comments)
        comment_batch = [
            (i, random.randint(1, config.total_posts), random.randint(1, config.users))
            for i in range(batch_start + 1, batch_end + 1)
        ]
        await db_connection.executemany(
            "INSERT INTO comments (id, post_id, user_id) VALUES (?, ?, ?)",
            comment_batch,
        )
        await db_connection.commit()

    return None


async def _insert_likes(
    db_connection: aiosqlite.Connection, total_likes: int, config: BenchmarkConfig
) -> None:
    """Generate and insert unique likes to avoid constraint violations."""
    unique_likes = set()
    while len(unique_likes) < total_likes:
        unique_likes.add(
            (random.randint(1, config.total_posts), random.randint(1, config.users))
        )

    # Insert in batches
    like_list = list(unique_likes)
    batch_size = 25_000

    for batch_start in range(0, len(like_list), batch_size):
        batch_end = min(batch_start + batch_size, len(like_list))
        like_batch = [
            (i + batch_start + 1, post_id, user_id)
            for i, (post_id, user_id) in enumerate(like_list[batch_start:batch_end])
        ]
        await db_connection.executemany(
            "INSERT INTO likes (id, post_id, user_id) VALUES (?, ?, ?)", like_batch
        )
        await db_connection.commit()

    return None


async def _create_database_indexes(db_connection: aiosqlite.Connection) -> None:
    """Create performance indexes."""
    indexes = [
        "CREATE INDEX idx_posts_user_id ON posts(user_id)",
        "CREATE INDEX idx_comments_post_id ON comments(post_id)",
        "CREATE INDEX idx_comments_user_id ON comments(user_id)",
        "CREATE INDEX idx_likes_post_id ON likes(post_id)",
        "CREATE INDEX idx_likes_user_id ON likes(user_id)",
    ]

    for index_sql in indexes:
        await db_connection.execute(index_sql)

    await db_connection.commit()

    return None


# =============================================================================
# Benchmark Executors
# =============================================================================


async def run_baseline_test(database_path: str, config: BenchmarkConfig) -> List[float]:
    """Baseline test: Create new connection for every query (typical approach)."""

    async def baseline_worker(request_count: int, worker_seed: int) -> List[float]:
        worker_random = random.Random(worker_seed)
        latencies = []

        for _ in range(request_count):
            post_id = worker_random.randint(1, config.total_posts)
            latencies.append(
                await _execute_query_with_new_connection(database_path, post_id)
            )

        return [latency for latency in latencies if latency > 0]

    tasks = [
        baseline_worker(config.requests_per_worker, worker_id)
        for worker_id in range(config.concurrent_workers)
    ]
    worker_results = await asyncio.gather(*tasks)

    return [
        latency for worker_latencies in worker_results for latency in worker_latencies
    ]


async def _execute_query_with_new_connection(database_path: str, post_id: int) -> float:
    """Execute single query with new connection, return latency or 0 if failed."""
    start_time = time.perf_counter()

    async with aiosqlite.connect(database_path) as connection:
        await connection.execute("PRAGMA journal_mode = WAL")
        await connection.execute("PRAGMA synchronous = NORMAL")
        await connection.execute("PRAGMA cache_size = 10000")

        async with connection.execute(BENCHMARK_QUERY, (post_id,)) as cursor:
            await cursor.fetchone()

    return time.perf_counter() - start_time


async def run_pool_test(database_path: str, config: BenchmarkConfig) -> List[float]:
    pool = SQLiteConnectionPool(
        connection_factory=lambda: create_sqlite_connection(database_path),
        pool_size=config.pool_size,
    )

    async with pool:

        async def pool_worker(request_count: int, worker_seed: int) -> List[float]:
            worker_random = random.Random(worker_seed)
            latencies = []

            for _ in range(request_count):
                post_id = worker_random.randint(1, config.total_posts)
                latencies.append(await _execute_query_with_pool(pool, post_id))

            return [
                latency for latency in latencies if latency > 0
            ]  # Filter failed requests

        tasks = [
            pool_worker(config.requests_per_worker, worker_id)
            for worker_id in range(config.concurrent_workers)
        ]
        worker_results = await asyncio.gather(*tasks)

    return [
        latency for worker_latencies in worker_results for latency in worker_latencies
    ]


async def _execute_query_with_pool(pool: SQLiteConnectionPool, post_id: int) -> float:
    start_time = time.perf_counter()

    async with pool.connection() as connection:
        async with connection.execute(BENCHMARK_QUERY, (post_id,)) as cursor:
            await cursor.fetchone()

    return time.perf_counter() - start_time


# =============================================================================
# Results Analysis
# =============================================================================


def display_benchmark_results(
    baseline_latencies: List[float],
    pool_latencies: List[float],
    baseline_time: float,
    pool_time: float,
    config: BenchmarkConfig,
) -> None:
    """Display comprehensive benchmark results and analysis."""
    print(f"\n{'=' * 50}")
    print("BENCHMARK RESULTS")
    print(f"{'=' * 50}")
    print(
        f"📋 {config.total_requests:,} requests • {config.concurrent_workers} workers • pool size {config.pool_size}"
    )
    baseline_stats = _calculate_latency_statistics(baseline_latencies)
    pool_stats = _calculate_latency_statistics(pool_latencies)
    rps = config.total_requests / baseline_time
    print(
        f"\nBaseline: {baseline_time:.1f}s • {rps:,.0f} req/s • {baseline_stats['avg']:.1f}ms avg • median: {baseline_stats['median']:.1f}ms • p90: {baseline_stats['p90']:.1f}ms • p99: {baseline_stats['p99']:.1f}ms"
    )
    rps = config.total_requests / pool_time
    print(
        f"Pool:     {pool_time:.1f}s • {rps:,.0f} req/s • {pool_stats['avg']:.1f}ms avg • median: {pool_stats['median']:.1f}ms • p90: {pool_stats['p90']:.1f}ms • p99: {pool_stats['p99']:.1f}ms"
    )
    print(f"{'=' * 50}")


def _calculate_latency_statistics(latencies: List[float]) -> dict:
    """Calculate latency statistics in milliseconds."""
    latencies_ms = np.array(latencies) * 1000
    return {
        "avg": np.mean(latencies_ms),
        "median": np.median(latencies_ms),
        "p90": np.percentile(latencies_ms, 90),
        "p99": np.percentile(latencies_ms, 99),
    }


# =============================================================================
# Runner
# =============================================================================


async def run_benchmark(config: BenchmarkConfig) -> None:
    """Run the complete benchmark comparing baseline vs pool performance."""

    timestamp = int(time.time())
    baseline_db_path = f"benchmark_baseline_{timestamp}.db"
    pool_db_path = f"benchmark_pool_{timestamp}.db"

    try:
        print(
            f"Configuration: {config.total_requests:,} requests, {config.concurrent_workers} workers, pool size {config.pool_size}"
        )

        print("⚙️  Creating two separate identical databases...")
        print(f"  - {baseline_db_path}")
        print(f"  - {pool_db_path}")
        print(
            f"  - {config.users:,} users, {config.total_posts:,} posts, {config.total_posts * 50:,} comments, {config.total_posts * 100:,} likes"
        )
        await create_benchmark_database(baseline_db_path, config)
        await create_benchmark_database(pool_db_path, config)
        # Brief pause to ensure databases are fully ready
        await asyncio.sleep(0.1)

        print(
            f"--- Running {config.total_requests:,} requests with {config.concurrent_workers} workers..."
        )

        # Run baseline test
        print("--- Testing baseline approach...", end=" ", flush=True)
        baseline_start = time.perf_counter()
        baseline_latencies = await run_baseline_test(baseline_db_path, config)
        baseline_time = time.perf_counter() - baseline_start
        print(f"Done ({baseline_time:.1f}s)")

        # Run pool test
        print("--- Testing connection pool...", end=" ", flush=True)
        pool_start = time.perf_counter()
        pool_latencies = await run_pool_test(pool_db_path, config)
        pool_time = time.perf_counter() - pool_start
        print(f"Done ({pool_time:.1f}s)")

        # Analyze results
        display_benchmark_results(
            baseline_latencies, pool_latencies, baseline_time, pool_time, config
        )

    finally:
        # Clean up databases
        for db_path in [baseline_db_path, pool_db_path]:
            for suffix in ["", "-wal", "-shm"]:
                try:
                    os.remove(f"{db_path}{suffix}")
                except FileNotFoundError:
                    pass


def create_argument_parser() -> argparse.ArgumentParser:
    """Create command line argument parser."""
    parser = argparse.ArgumentParser(
        description="aiosqlitepool performance benchmark",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--requests",
        "-n",
        type=int,
        default=10_000,
        help="Total number of database requests to execute",
    )
    parser.add_argument(
        "--workers", "-w", type=int, default=100, help="Number of concurrent workers"
    )
    parser.add_argument(
        "--pool-size", "-p", type=int, default=5, help="Connection pool size"
    )
    parser.add_argument(
        "--users", type=int, default=1_000, help="Number of users in test database"
    )
    parser.add_argument(
        "--posts-per-user", type=int, default=10, help="Number of posts per user"
    )

    return parser


if __name__ == "__main__":
    print("aiosqlitepool performance benchmark")
    print("=" * 20)
    # Parse command line arguments
    parser = create_argument_parser()
    args = parser.parse_args()
    # Create configuration
    config = BenchmarkConfig(
        total_requests=args.requests,
        concurrent_workers=args.workers,
        pool_size=args.pool_size,
        users=args.users,
        posts_per_user=args.posts_per_user,
    )
    # Run benchmark
    asyncio.run(run_benchmark(config))
