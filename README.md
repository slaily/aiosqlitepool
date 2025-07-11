# aiosqlitepool

<p>
<a>
    <img src="https://img.shields.io/badge/stability-stable-green.svg" alt="Stable version">
</a>
<a href="https://pypi.org/project/aiosqlitepool" target="_blank">
    <img src="https://img.shields.io/pypi/v/aiosqlitepool?color=%2334D058&label=pypi%20package" alt="Package version">
</a>
<a href="https://pypi.org/project/aiosqlitepool" target="_blank">
    <img src="https://img.shields.io/pypi/pyversions/aiosqlitepool.svg?color=%2334D058" alt="Supported Python versions">
</a>
<a href="https://github.com/slaily/aiosqlitepool/blob/main/LICENSE" target="_blank">
    <img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="Supported Python versions">
</a>
</p>

`aiosqlitepool` is a high-performance connection pool for asyncio SQLite applications. By managing a pool of reusable database connections, it eliminates connection overhead and delivers significant performance gains.

**Important**: `aiosqlitepool` is not a SQLite database driver.

It's a performance-boosting layer that works *with* an asyncio driver like [aiosqlite](https://github.com/omnilib/aiosqlite), not as a replacement for it.

`aiosqlitepool` in three points:

* **Eliminates connection overhead**: It avoids repeated database connection setup (syscalls, memory allocation) and teardown (syscalls, deallocation) by reusing long-lived connections.
* **Faster queries via "hot" cache**: Long-lived connections keep SQLite's in-memory page cache "hot." This serves frequently requested data directly from memory, speeding up repetitive queries and reducing I/O operations.
* **Maximizes concurrent throughput**: Allows your application to process significantly more database queries per second under heavy load.

## Table of contents

- [Installation](#installation)
- [Usage](#usage)
  - [Basic usage](#basic-usage)
  - [Using as Context Manager](#using-as-context-manager)
  - [High-performance SQLite connection configuration](#high-performance-sqlite-connection-configuration)
  - [FastAPI integration](#fastapi-integration)
- [Configuration](#configuration)
- [How it works](#how-it-works)
- [Do you need a connection pool with SQLite?](#do-you-need-a-connection-pool-with-sqlite)
- [Benchmarks](#benchmarks)
  - [Load test](#load-test)
  - [Connection overhead](#connection-overhead)
- [Compatibility](#compatibility)
  - [Officially supported drivers](#officially-supported-drivers)
  - [Using other drivers](#using-other-drivers)
- [License](#license)
 
## Installation

`aiosqlitepool` requires the `aiosqlite` driver to be installed as a peer dependency.

Install with your preferred package manager:

**pip**
```bash
pip install aiosqlite aiosqlitepool
```

**uv**
```bash
uv add aiosqlite aiosqlitepool
```

**Poetry**
```bash
poetry add aiosqlite aiosqlitepool
```

## Usage

### Basic usage

You must provide a `connection_factory` - an async function that creates and returns a database connection:

```python
import asyncio
import aiosqlite

from aiosqlitepool import SQLiteConnectionPool


async def connection_factory():
    return await aiosqlite.connect("basic.db")


async def main():
    pool = SQLiteConnectionPool(connection_factory)
    
    async with pool.connection() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS users (name TEXT)")
        await conn.execute("INSERT INTO users VALUES (?)", ("Alice",))
        # You must handle transaction management yourself
        await conn.commit()
        cursor = await conn.execute("SELECT name FROM users")
        row = await cursor.fetchone()
        print(f"Found user: {row[0]}")
    
    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
```

**Note**: The pool manages connections, not transactions. You're responsible for calling `commit()` or `rollback()` as needed. The pool ensures connections are safely reused but doesn't interfere with your transaction logic.

### Using as Context Manager

The pool can be used as an async context manager for automatic cleanup:

```python
async def main():
    async with SQLiteConnectionPool(create_connection) as pool:
        async with pool.connection() as conn:
            # Do database work
            pass
    # Pool is automatically closed
```

### High-performance SQLite connection configuration

For high-performance applications, configure your connection factory with optimized SQLite pragmas.

```python
import aiosqlite

from aiosqlitepool import SQLiteConnectionPool


async def sqlite_connection() -> aiosqlite.Connection:
    # Connect to your database
    conn = await aiosqlite.connect("your_database.db")
    # Apply high-performance pragmas
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute("PRAGMA synchronous = NORMAL")
    await conn.execute("PRAGMA cache_size = 10000")
    await conn.execute("PRAGMA temp_store = MEMORY")
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute("PRAGMA mmap_size = 268435456")
    
    return conn


async def main():
    # Initialize the connection pool with your high-performance connection factory
    pool = SQLiteConnectionPool(
        connection_factory=sqlite_connection,
    )
    
    # Use the pool
    async with pool.connection() as conn:
        # Your database operations here
        # cursor = await conn.execute("SELECT ...")
        # rows = await cursor.fetchall()
        pass
    
    # Clean up
    await pool.close()
```

**`PRAGMA journal_mode = WAL`** - Writes go to a separate WAL file, reads continue from main database. Multiple readers can work simultaneously with one writer.

**`PRAGMA synchronous = NORMAL`** - SQLite syncs to disk at critical moments, but not after every write. ~2-3x faster writes than `FULL` synchronization.

**`PRAGMA cache_size = 10000`** - Keeps 10,000 database pages (~40MB) in memory. Frequently accessed data served from RAM, not disk

**`PRAGMA temp_store = MEMORY`** - Stores temporary tables, indexes, and sorting operations in RAM. Eliminates disk I/O for temporary operations

**`PRAGMA foreign_keys = ON`** - Enforces foreign key constraints automatically. Prevents data corruption, reduces application-level checks

**`PRAGMA mmap_size = 268435456`** - Maps database file directly into process memory space. Reduces system calls, faster access to large databases


### FastAPI integration

This section demonstrates an effective pattern for integrating `aiosqlitepool` with [FastAPI](https://fastapi.tiangolo.com/) applications. 

The pattern addresses three key requirements:

1. **Lifecycle management**: The pool is created during application startup and gracefully closed on shutdown using FastAPI's `lifespan` context manager
2. **Global access**: The pool is stored in the application's state, making it accessible to all route handlers
3. **Dependency injection**: A reusable dependency function provides clean access to pooled connections with automatic resource management

```python
import asyncio

from typing import AsyncGenerator
from contextlib import asynccontextmanager

import aiosqlite

from aiosqlitepool import SQLiteConnectionPool
from fastapi import (
    Request,
    Depends, 
    FastAPI, 
    HTTPException, 
)


async def sqlite_connection() -> aiosqlite.Connection:
    """A factory for creating new connections."""
    conn = await aiosqlite.connect("app.db")

    return conn


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage the connection pool's lifecycle.
    The pool is created when the application starts and gracefully closed when it stops.
    """
    db_pool = SQLiteConnectionPool(connection_factory=sqlite_connection, pool_size=10)
    app.state.db_pool = db_pool
    yield
    await db_pool.close()


app = FastAPI(lifespan=lifespan)


async def get_db_connection(request: Request) -> AsyncGenerator[Connection]:
    """
    A dependency that provides a connection from the pool.
    It accesses the pool from the application state.
    """
    db_pool = request.app.state.db_pool

    async with db_pool.connection() as conn:
        yield conn


@app.get("/users/{user_id}")
async def get_user(
    user_id: int, db_conn: Connection = Depends(get_db_connection)
) -> dict[str, any]:
    cursor = await db_conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user = await cursor.fetchone()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return dict(user)
```

### Configuration

`SQLiteConnectionPool` accepts these parameters:

**Required parameters**
* **`connection_factory`** - An async function that creates and returns a new database connection. This function will be called whenever the pool needs to create a new connection.

**Optional parameters**
* **`pool_size`** (int) - Maximum number of connections to maintain in the pool (default: `5`)

* **`acquisition_timeout`** (int) - Maximum seconds to wait for an available connection (default: `30`)

* **`idle_timeout`** (int) - Maximum seconds a connection can remain idle before replacement (default: `86400` - 24 hours)

**Recommended configurations**

Most web applications work well with these settings:

```python
pool = SQLiteConnectionPool(
    connection_factory,
    pool_size=10,
    acquisition_timeout=30
)
```

For read-heavy workloads like analytics or reporting:

```python
pool = SQLiteConnectionPool(
    connection_factory,
    pool_size=20,
    acquisition_timeout=15
)
```

For write-heavy workloads:

```python
pool = SQLiteConnectionPool(
    connection_factory,
    pool_size=5,
    acquisition_timeout=60
)
```

## How it works

The pool automatically:

* Creates connections on-demand up to the pool size limit
* Reuses idle connections to avoid creation overhead
* Performs health checks to detect broken connections
* Rolls back any uncommitted transactions when connections are returned
* Replaces connections that have been idle too long

## Do you need a connection pool with SQLite?

For server-based databases like PostgreSQL or MySQL, the answer is always yes. Connecting over a network is slow and expensive. A connection pool is a critical pattern to minimize latency and manage server resources.

But SQLite is different. It's an embedded, in-process database. There's no network, no sockets, just a file on disk. The overhead of creating a connection is measured in microseconds, not even milliseconds. 

So, is a connection pool just needless complexity?

The primary challenge with SQLite in a concurrent environment (like an `asyncio` web application) is not connection time, but **write contention**. SQLite uses a database-level lock for writes. When multiple asynchronous tasks try to write to the database simultaneously through their own separate connections, they will collide. This contention leads to a cascade of `SQLITE_BUSY` or `SQLITE_LOCKED` errors.

Most applications will not encounter these issues, only a small percentage under heavy load!

Here's a quick checklist. Use `aiosqlitepool` if:

- **Your service handles steady web traffic**: Your application serves more than **5-10 requests per second** from the database.
- **Your workers process high-throughput jobs**: Your background workers run more than **~30 queries per second**.
- **Your application requires predictable low latency**: Your service operates under a tight performance budget (e.g., p99 latency < 50ms).
- **You aim for a minimal footprint**: You design your applications to be resource-efficient, knowing that reducing CPU and I/O load contributes to leaner, more sustainable infrastructure.

You don't need `aiosqlitepool` if your application is:
- A **short-lived script** that runs a few queries and exits.
- A **very low-traffic service** with fewer than a few requests per minute.

## Benchmarks

All benchmarks performed on a realistic database with:

- 1.2M users 
- 120K posts 
- 6M comments
- 12M likes

### Load test

*1,000 concurrent requests across 100 workers*

| Metric | Without Pool | With Pool | Improvement |
|--------|-------------|-----------|-------------|
| **Queries/sec** | 3,325 | 5,731 | **+72%** |
| **Average latency** | 28.98ms | 17.13ms | **-41%** |
| **Median latency** | 28.10ms | 13.57ms | **-52%** |
| **P90 latency** | 37.39ms | 18.25ms | **-51%** |
| **P99 latency** | 42.17ms | 58.76ms | Variable* |

*\*P99 latency shows pool contention under extreme load (100 workers, pool size 100) where 1% of requests must wait for connection availability*

**Key takeaway**: In realistic concurrent scenarios, connection pooling delivers 1.7x throughput improvement and 2x faster response times for 99% of requests.

### Connection overhead

*10,000 simple SELECT operations across 5 workers*

| Approach | Avg Latency | Median Latency | Total Time | Operations/sec |
|----------|-------------|----------------|------------|----------------|
| **Open/close per query** | 1,019μs | 1,006μs | 2.04s | 4,902 |
| **Persistent connections** | 396μs | 389μs | 0.79s | 12,658 |
| **Improvement** | **-61%** | **-61%** | **-61%** | **+158%** |

**Pure connection overhead**: Each connection create/destroy cycle costs **623 microseconds** (1,019 - 396 = 623μs) of pure overhead per database operation.

## Compatibility

`aiosqlitepool` is designed to be a flexible pooling layer that works with different asyncio SQLite drivers.


To be compatible, a connection object from a driver must have implemented the following three `async` methods:

```python
class Connection:
    async def execute(self, *args, **kwargs):
        ...

    async def rollback(self) -> None:
        ...

    async def close(self) -> None:
        ...
```

**Note on `commit`**: The `commit` method is intentionally not part of the protocol. Transaction management is considered the responsibility of the application developer, not the pool. `aiosqlitepool` never commits on your behalf.

### Officially supported drivers

The following libraries are tested and confirmed to work out-of-the-box with `aiosqlitepool`:

*   **[aiosqlite](https://github.com/omnilib/aiosqlite)**

### Using other drivers

If you are using another asyncio SQLite library that follows the protocol, it should work seamlessly. Just pass your driver's connection function to the `connection_factory`.

If you encounter an issue with a specific driver, please let us know by [opening a GitHub issue](https://github.com/slaily/aiosqlitepool/issues).

## License

aiosqlitepool is available under the MIT License.
