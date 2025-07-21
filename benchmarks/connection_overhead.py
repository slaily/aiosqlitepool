"""
aiosqlitepool vs open/close connection overhead benchmark

A simple benchmark demonstrating that connection pooling is faster
than opening/closing connections for every database request.

Compares:
- Open/close: New connection per query (typical naive approach)
- Pool: Reused connections from SQLiteConnectionPool

Usage:
    python benchmarks/connection_overhead.py                            # Default: 10k requests, 100 workers, pool size 5
    python benchmarks/connection_overhead.py --requests 100000          # 100k requests per worker with 100 workers
    python benchmarks/connection_overhead.py --requests 1000000 -p 100  # 1M requests per worker with 100 workers, pool size 100
    python benchmarks/connection_overhead.py --help                     # All options
"""

import os
import time
import asyncio
import argparse

from typing import List
from pathlib import Path
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

    @property
    def requests_per_worker(self) -> int:
        return self.total_requests // self.concurrent_workers


# =============================================================================
# Database Setup
# =============================================================================

BENCHMARK_QUERY = "SELECT 1"
READ_ONLY_PRAGMAS = [
    "PRAGMA synchronous = OFF",  # No disk syncing needed for reads
    "PRAGMA journal_mode = OFF",  # No journaling for read-only
    "PRAGMA query_only = ON",  # Prevent any database modifications
]


async def setup_database(db_path: str) -> None:
    """Create benchmark database file."""
    # Clean up any existing database files
    for suffix in ("", "-wal", "-shm"):
        file_path = f"{db_path}{suffix}"
        if os.path.exists(file_path):
            os.remove(file_path)

    async with aiosqlite.connect(db_path):
        pass


def cleanup_database(db_path: str) -> None:
    """Remove database files."""
    for suffix in ("", "-wal", "-shm"):
        file_path = f"{db_path}{suffix}"
        try:
            if os.path.exists(file_path):
                os.remove(file_path)
        except OSError:
            pass


# =============================================================================
# Benchmark Executors
# =============================================================================


async def run_open_close_benchmark(
    db_path: str, config: BenchmarkConfig
) -> List[float]:
    """Benchmark opening/closing connection for each query."""

    async def worker(requests_count: int) -> List[float]:
        latencies = []

        for _ in range(requests_count):
            start_time = time.perf_counter()

            async with aiosqlite.connect(f"file:{db_path}") as db:
                for pragma in READ_ONLY_PRAGMAS:
                    await db.execute(pragma)
                await db.execute(BENCHMARK_QUERY)

            latencies.append(time.perf_counter() - start_time)

        return latencies

    tasks = [
        worker(config.requests_per_worker) for _ in range(config.concurrent_workers)
    ]
    worker_results = await asyncio.gather(*tasks)

    return [
        latency for worker_latencies in worker_results for latency in worker_latencies
    ]


async def run_pool_benchmark(db_path: str, config: BenchmarkConfig) -> List[float]:
    """Benchmark using connection pool for queries."""

    async def connection_factory():
        conn = await aiosqlite.connect(f"file:{db_path}")
        for pragma in READ_ONLY_PRAGMAS:
            await conn.execute(pragma)
        return conn

    pool = SQLiteConnectionPool(
        connection_factory=connection_factory, pool_size=config.pool_size
    )

    async with pool:

        async def worker(
            requests_count: int,
        ) -> List[float]:
            latencies = []

            for _ in range(requests_count):
                start_time = time.perf_counter()

                async with pool.connection() as conn:
                    await conn.execute(BENCHMARK_QUERY)

                latencies.append(time.perf_counter() - start_time)

            return latencies

        tasks = [
            worker(config.requests_per_worker) for _ in range(config.concurrent_workers)
        ]
        worker_results = await asyncio.gather(*tasks)

    return [
        latency for worker_latencies in worker_results for latency in worker_latencies
    ]


# =============================================================================
# Results Analysis
# =============================================================================


def calculate_statistics(latencies: List[float]) -> dict:
    """Calculate latency statistics in microseconds."""
    if not latencies:
        return {}

    latencies_us = np.array(latencies) * 1_000_000

    return {
        "count": len(latencies),
        "avg": float(np.mean(latencies_us)),
        "median": float(np.percentile(latencies_us, 50)),
        "p90": float(np.percentile(latencies_us, 90)),
        "p99": float(np.percentile(latencies_us, 99)),
    }


def display_results(
    open_close_latencies: List[float],
    pool_latencies: List[float],
    open_close_time: float,
    pool_time: float,
    config: BenchmarkConfig,
) -> None:
    """Display benchmark results with clean formatting."""
    open_close_stats = calculate_statistics(open_close_latencies)
    pool_stats = calculate_statistics(pool_latencies)

    open_close_rps = config.total_requests / open_close_time
    pool_rps = config.total_requests / pool_time

    print(f"\n{'=' * 60}")
    print("BENCHMARK RESULTS")
    print(f"{'=' * 60}")
    print(
        f"📊 {config.total_requests:,} requests • {config.concurrent_workers} workers • pool size {config.pool_size}"
    )
    print()

    print(
        f"Open/Close: {open_close_time:.1f}s • {open_close_rps:,.0f} req/s • "
        f"{open_close_stats['avg']:.0f}µs avg • median: {open_close_stats['median']:.0f}µs • p90: {open_close_stats['p90']:.0f}µs • p99: {open_close_stats['p99']:.0f}µs"
    )

    print(
        f"Pool:       {pool_time:.1f}s • {pool_rps:,.0f} req/s • "
        f"{pool_stats['avg']:.0f}µs avg • median: {pool_stats['median']:.0f}µs • p90: {pool_stats['p90']:.0f}µs • p99: {pool_stats['p99']:.0f}µs"
    )


# =============================================================================
# Main Runner
# =============================================================================


async def run_benchmark(config: BenchmarkConfig) -> None:
    """Run the complete benchmark comparing open/close vs pool."""
    db_path = Path(__file__).with_name("overhead_benchmark.db").as_posix()

    try:
        print("⚙️  Setting up benchmark database...")
        await setup_database(db_path)

        print(
            f"🏃 Running {config.total_requests:,} requests with {config.concurrent_workers} workers..."
        )

        # Run pool benchmark
        print("--- Testing connection pool...", end=" ", flush=True)
        start_time = time.perf_counter()
        pool_latencies = await run_pool_benchmark(db_path, config)
        pool_time = time.perf_counter() - start_time
        print(f"Done ({pool_time:.1f}s)")
        # Run open/close benchmark
        print("--- Testing open/close approach...", end=" ", flush=True)
        start_time = time.perf_counter()
        open_close_latencies = await run_open_close_benchmark(db_path, config)
        open_close_time = time.perf_counter() - start_time
        print(f"Done ({open_close_time:.1f}s)")

        # Display results
        display_results(
            open_close_latencies, pool_latencies, open_close_time, pool_time, config
        )

    finally:
        cleanup_database(db_path)


def create_argument_parser() -> argparse.ArgumentParser:
    """Create command line argument parser."""
    parser = argparse.ArgumentParser(
        description="aiosqlitepool connection overhead benchmark",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "-n",
        "--requests",
        type=int,
        default=10_000,
        help="Total number of database requests",
    )
    parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=100,
        help="Number of concurrent workers",
    )
    parser.add_argument(
        "-p", "--pool-size", type=int, default=5, help="Connection pool size"
    )

    return parser


if __name__ == "__main__":
    print("aiosqlitepool connection overhead benchmark")
    print("=" * 50)
    parser = create_argument_parser()
    args = parser.parse_args()
    config = BenchmarkConfig(
        total_requests=args.requests,
        concurrent_workers=args.concurrency,
        pool_size=args.pool_size,
    )
    asyncio.run(run_benchmark(config))
