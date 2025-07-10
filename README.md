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
</p>

`aiosqlitepool` is a high-performance connection pool for asyncio SQLite applications. By managing a pool of reusable database connections, it eliminates connection overhead and delivers significant performance gains.

**Important**: `aiosqlitepool` is not a SQLite database driver.

It's a performance-boosting layer that works *with* an asyncio driver like [aiosqlite](https://github.com/omnilib/aiosqlite), not as a replacement for it.


## Table of Contents

- [License](#license)

aiosqlitepool in three points:

* **Eliminates connection overhead**: It avoids repeated database connection setup (syscalls, memory allocation) and teardown (syscalls, deallocation) by reusing long-lived connections.
* **Faster queries via "hot" cache**: Long-lived connections keep SQLite's in-memory page cache "hot." This serves frequently requested data directly from memory, speeding up repetitive queries and reducing I/O operations.
* **Maximizes concurrent throughput**: Allows your application to process significantly more database queries per second under heavy load.
 
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

## Quick Start

```python
import asyncio
import aiosqlite
from aiosqlitepool import SQLiteConnectionPool

async def connection_factory():
    return await aiosqlite.connect("example.db")

async def main():
    pool = SQLiteConnectionPool(connection_factory)
    
    async with pool.connection() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS users (name TEXT)")
        await conn.execute("INSERT INTO users VALUES (?)", ("Alice",))
        # You must handle transaction management yourself
        await conn.commit()
    
    async with pool.connection() as conn:
        cursor = await conn.execute("SELECT name FROM users")
        row = await cursor.fetchone()
        print(f"Found user: {row[0]}")
    
    await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
```

**Note**: The pool manages connections, not transactions. You're responsible for calling `commit()` or `rollback()` as needed. The pool ensures connections are safely reused but doesn't interfere with your transaction logic.

## Usage

### Basic Usage

You must provide a `connection_factory` - an async function that creates and returns a database connection:

```python
import asyncio
import aiosqlite

from aiosqlitepool import SQLiteConnectionPool


async def create_connection():
    return await aiosqlite.connect("my_app.db")


async def main():
    pool = SQLiteConnectionPool(create_connection)
    
    # The pool manages connections automatically
    async with pool.connection() as conn:
        cursor = await conn.execute("SELECT * FROM users")
        users = await cursor.fetchall()
        # No commit needed for read operations
    
    # For write operations, you handle transactions
    async with pool.connection() as conn:
        try:
            await conn.execute("INSERT INTO users VALUES (?)", ("Bob",))
            await conn.execute("INSERT INTO users VALUES (?)", ("Carol",))
            await conn.commit()  # Your responsibility to commit
        except Exception:
            await conn.rollback()  # Your responsibility to rollback
            raise
    
    await pool.close()
```

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

### FastAPI Integration

Here are two common patterns for using aiosqlitepool with FastAPI:

#### Manual Dependency Injection

```python
from fastapi import FastAPI, Depends, HTTPException
import aiosqlite
from aiosqlitepool import SQLiteConnectionPool

app = FastAPI()

# Global pool instance
pool = None

async def create_connection():
    conn = await aiosqlite.connect("app.db")
    conn.row_factory = aiosqlite.Row
    return conn

@app.on_event("startup")
async def startup():
    global pool
    pool = SQLiteConnectionPool(create_connection, pool_size=10)

@app.on_event("shutdown") 
async def shutdown():
    if pool:
        await pool.close()

def get_pool():
    return pool

@app.get("/users/{user_id}")
async def get_user(user_id: int, pool: SQLiteConnectionPool = Depends(get_pool)):
    async with pool.connection() as conn:
        cursor = await conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        user = await cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return dict(user)

@app.post("/users")
async def create_user(name: str, pool: SQLiteConnectionPool = Depends(get_pool)):
    async with pool.connection() as conn:
        try:
            cursor = await conn.execute(
                "INSERT INTO users (name) VALUES (?) RETURNING id", (name,)
            )
            result = await cursor.fetchone()
            await conn.commit()  # Your responsibility to commit
            return {"id": result["id"], "name": name}
        except Exception:
            await conn.rollback()  # Your responsibility to rollback
            raise HTTPException(status_code=500, detail="Failed to create user")
```

#### Context Manager Pattern

```python
from fastapi import FastAPI, HTTPException
from contextlib import asynccontextmanager
import aiosqlite
from aiosqlitepool import SQLiteConnectionPool

async def create_connection():
    conn = await aiosqlite.connect("app.db")
    conn.row_factory = aiosqlite.Row
    return conn

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    pool = SQLiteConnectionPool(create_connection, pool_size=10)
    app.state.pool = pool
    yield
    # Shutdown
    await pool.close()

app = FastAPI(lifespan=lifespan)

@app.get("/users/{user_id}")
async def get_user(user_id: int):
    async with app.state.pool.connection() as conn:
        cursor = await conn.execute("SELECT * FROM users WHERE id = ?", (user_id,))
        user = await cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        return dict(user)

@app.post("/users")
async def create_user(name: str):
    async with app.state.pool.connection() as conn:
        try:
            cursor = await conn.execute(
                "INSERT INTO users (name) VALUES (?) RETURNING id", (name,)
            )
            result = await cursor.fetchone()
            await conn.commit()  # Your responsibility to commit
            return {"id": result["id"], "name": name}
        except Exception:
            await conn.rollback()  # Your responsibility to rollback
            raise HTTPException(status_code=500, detail="Failed to create user")
```

## How It Works

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

## Configuration

`SQLiteConnectionPool` accepts these parameters:

* `connection_factory` (required): Async function that returns a database connection
* `pool_size` (int): Maximum number of connections in the pool (default: 20)
* `acquisition_timeout` (int): Seconds to wait for a connection (default: 30)
* `idle_timeout` (int): Seconds before idle connections are replaced (default: 86400)

### Recommended Settings

**Web API (FastAPI/Django):**
```python
pool = SQLiteConnectionPool(
    connection_factory,
    pool_size=10,  # Usually enough for most web apps
    acquisition_timeout=30,  # HTTP timeout compatible
    idle_timeout=3600  # 1 hour - good for web traffic patterns
)
```

**Background Workers/Heavy Load:**
```python
pool = SQLiteConnectionPool(
    connection_factory,
    pool_size=50,  # More connections for concurrent processing
    acquisition_timeout=60,  # Longer timeout for batch jobs
    idle_timeout=7200  # 2 hours - longer running processes
)
```

**Low Traffic Applications:**
```python
pool = SQLiteConnectionPool(
    connection_factory,
    pool_size=5,  # Fewer connections needed
    acquisition_timeout=10,  # Quick timeout for responsiveness
    idle_timeout=1800  # 30 minutes - conserve resources
)
```

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
