# aiosqlitepool

aiosqlitepool is a connection pool for asyncio SQLite database applications. Instead of creating a new database connection for every operation, it maintains a pool of reusable connections that dramatically improve your application's performance and responsiveness.

**Important**: aiosqlitepool does not implement SQLite driver functionality. It's a complementary pooling layer built on top of existing asyncio SQLite libraries like [aiosqlite](https://github.com/omnilib/aiosqlite). You still need an underlying SQLite driver - aiosqlitepool just makes it faster by pooling connections.

It works as a layer on top of any asyncio SQLite driver that implements a simple protocol. Currently tested with [aiosqlite](https://github.com/omnilib/aiosqlite), but designed to work with other compatible libraries.

## Table of Contents

- [Why Connection Pooling?](#why-connection-pooling)
- [Quick Start](#quick-start)
- [Installation](#installation)
- [Usage](#usage)
  - [Basic Usage](#basic-usage)
  - [Row Factory Support](#row-factory-support)
  - [Using as Context Manager](#using-as-context-manager)
  - [FastAPI Integration](#fastapi-integration)
- [Configuration](#configuration)
- [Performance Benchmarks](#performance-benchmarks)
- [Common Issues & Solutions](#common-issues--solutions)
- [Connection Protocol](#connection-protocol)
- [How It Works](#how-it-works)
- [License](#license)

aiosqlitepool in three points:

* **Eliminates connection overhead**: Reuses long-lived connections instead of creating new ones for each operation
* **Simplifies configuration**: Connections stay configured with your pragma settings and ready to use
* **Improves concurrent performance**: Handles more requests with the same hardware through efficient connection reuse

## When You Need This

You should use aiosqlitepool if your application:

- Makes frequent database queries (more than a few per minute)
- Handles concurrent requests (web APIs, background workers)
- Uses pragma settings like WAL mode or custom cache sizes
- Needs predictable database response times under load

**Skip it if**: You're building a CLI tool that makes 1-2 database calls and exits.

## Why Connection Pooling?

**The Problem**: Opening a new SQLite connection for every database operation is expensive:

```python
# Without pooling - MEASURED: ~29ms average
async def get_user(user_id):
    conn = await aiosqlite.connect("app.db")  # Connection overhead
    await conn.execute("PRAGMA journal_mode=WAL")  # Setup overhead  
    cursor = await conn.execute("SELECT * FROM users WHERE id=?", (user_id,))
    user = await cursor.fetchone()
    await conn.close()  # Cleanup overhead
    return user  # Measured: 28.98ms average in concurrent load
```

**The Solution**: Reuse connections from a pool:

```python
# With pooling - MEASURED: ~17ms average  
async def get_user(user_id):
    async with pool.connection() as conn:  # Fast: connection already ready
        cursor = await conn.execute("SELECT * FROM users WHERE id=?", (user_id,))
        return await cursor.fetchone()  # Measured: 17.13ms average in concurrent load
```

**Real Impact**: In our benchmark with 100 concurrent workers on a database with millions of records, connection pooling delivered 5,731 requests/second vs 3,325 without pooling - a **72% improvement in throughput** for the same hardware.

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

## Installation

```bash
pip install aiosqlitepool
```

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

### Row Factory Support

You can configure connections with row factories or other settings in your connection factory:

```python
import aiosqlite
from aiosqlitepool import SQLiteConnectionPool

async def create_connection_with_row_factory():
    conn = await aiosqlite.connect("my_app.db")
    conn.row_factory = aiosqlite.Row
    return conn

async def main():
    pool = SQLiteConnectionPool(create_connection_with_row_factory)
    
    async with pool.connection() as conn:
        cursor = await conn.execute("SELECT id, name FROM users WHERE id = ?", (1,))
        user = await cursor.fetchone()
        print(f"User: {user['name']}")  # Access by column name
    
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

## Configuration

`SQLiteConnectionPool` accepts these parameters:

* `connection_factory` (required): Async function that returns a database connection
* `pool_size` (int): Maximum number of connections in the pool (default: 20)
* `acquisition_timeout` (int): Seconds to wait for a connection (default: 30)
* `idle_timeout` (int): Seconds before idle connections are replaced (default: 86400)

### Recommended Settings by Use Case

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

## Performance Benchmarks

All benchmarks performed on a realistic database with 1.2M users, 120K posts, 6M comments, and 12M likes.

### Real-World Load Test

*1,000 concurrent requests across 100 workers*

| Metric | Without Pool | With Pool | Improvement |
|--------|-------------|-----------|-------------|
| **Requests/sec** | 3,325 | 5,731 | **+72%** |
| **Average latency** | 28.98ms | 17.13ms | **-41%** |
| **Median latency** | 28.10ms | 13.57ms | **-52%** |
| **P90 latency** | 37.39ms | 18.25ms | **-51%** |
| **P99 latency** | 42.17ms | 58.76ms | Variable* |

*\*P99 latency shows pool contention under extreme load (100 workers, pool size 100) where 1% of requests must wait for connection availability*

**Key takeaway**: In realistic concurrent scenarios, connection pooling delivers 1.7x throughput improvement and 2x faster response times for 99% of requests.

### Connection Overhead Analysis

*Micro-benchmark: 10,000 simple SELECT operations across 5 workers*

| Approach | Avg Latency | Median Latency | Total Time | Operations/sec |
|----------|-------------|----------------|------------|----------------|
| **Open/close per query** | 1,019μs | 1,006μs | 2.04s | 4,902 |
| **Persistent connections** | 396μs | 389μs | 0.79s | 12,658 |
| **Improvement** | **-61%** | **-61%** | **-61%** | **+158%** |

**Pure connection overhead**: Each connection create/destroy cycle costs **623μs** (1,019 - 396 = 623μs) of pure overhead per database operation.

### Performance Impact Summary

Without pooling, each database operation requires:
```
Connect → Apply pragmas → Execute query → Close = ~29ms average
```

With pooling:
```
Get pooled connection → Execute query → Return to pool = ~17ms average  
```

**Scaling impact**: This 623μs per operation overhead compounds with usage:
- 100 queries/second = 62ms of pure connection overhead per second
- 1,000 queries/second = 623ms of pure connection overhead per second  
- In high-throughput applications, pooling becomes essential for maintaining responsiveness

## Connection Protocol

aiosqlitepool works with any connection object that implements:

```python
async def execute(*args, **kwargs): ...
async def rollback(): ...
async def close(): ...
```

This makes it compatible with aiosqlite and other async SQLite libraries.

## Common Issues & Solutions

**"Pool connection timeout" errors:**
- Increase `pool_size` if you have high concurrency
- Check for forgotten `await` keywords in your code
- Make sure you're not holding connections too long

**Database locked errors:**
- Ensure you're calling `commit()` or `rollback()` in all code paths
- Check that connections are properly returned to the pool (use `async with`)
- Consider increasing `acquisition_timeout` for heavy workloads

**Memory usage growing over time:**
- Reduce `idle_timeout` to close idle connections sooner
- Reduce `pool_size` if you have fewer concurrent operations than expected
- Ensure `await pool.close()` is called during shutdown

**Slower than expected performance:**
- Make sure you're reusing the same pool instance across requests
- Verify your `connection_factory` isn't doing expensive setup repeatedly
- Check that you're not creating new pools for each operation

## How It Works

The pool automatically:

* Creates connections on-demand up to the pool size limit
* Reuses idle connections to avoid creation overhead
* Performs health checks to detect broken connections
* Rolls back any uncommitted transactions when connections are returned
* Replaces connections that have been idle too long

## License

aiosqlitepool is available under the MIT License.
