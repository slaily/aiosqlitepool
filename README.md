# aiosqlitepool

aiosqlitepool is a connection pool for asyncio SQLite database applications. It manages a pool of database connections to reduce the overhead of constantly opening and closing SQLite connections in concurrent applications.

**Important**: aiosqlitepool does not implement SQLite driver functionality. It's a complementary pooling layer built on top of existing asyncio SQLite libraries like [aiosqlite](https://github.com/omnilib/aiosqlite). You still need an underlying SQLite driver - aiosqlitepool just makes it faster by pooling connections.

It works as a layer on top of any asyncio SQLite driver that implements a simple protocol. Currently tested with [aiosqlite](https://github.com/omnilib/aiosqlite), but designed to work with other compatible libraries.

aiosqlitepool in three points:

* Reuses long-lived connections instead of creating new ones for each operation
* Eliminates repetitive connection setup overhead like applying pragmas
* Improves performance through connection-level caching and higher operations per second

## Why Use Connection Pooling?

Opening and closing SQLite connections for every operation creates significant overhead. Each new connection requires:

- Opening the database file
- Applying pragma settings (WAL mode, cache size, etc.)
- Parsing and compiling SQL statements from scratch
- Initializing internal SQLite structures
- Loading database pages into memory

Connection pooling eliminates this overhead by keeping connections alive and reusing them. Long-lived connections also benefit from SQLite's internal caching mechanisms, resulting in better performance and higher throughput for your application.

## A Simple Example

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

## Connection Protocol

aiosqlitepool works with any connection object that implements:

```python
async def execute(*args, **kwargs): ...
async def rollback(): ...
async def close(): ...
```

This makes it compatible with aiosqlite and other async SQLite libraries.

## How It Works

The pool automatically:

* Creates connections on-demand up to the pool size limit
* Reuses idle connections to avoid creation overhead
* Performs health checks to detect broken connections
* Rolls back any uncommitted transactions when connections are returned
* Replaces connections that have been idle too long

## License

aiosqlitepool is available under the MIT License.
