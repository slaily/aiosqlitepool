# aiosqlitepool

A robust, high-performance asynchronous connection pool for SQLite.

`aiosqlitepool` provides a straightforward and efficient way to manage `aiosqlite` connections. It's designed for performance, type safety, and ease of use in modern asynchronous Python applications, with a strong focus on production-readiness.

## Features

- **Flexible Initialization**: Simple path for file-based databases, and advanced `conn_factory` support for custom connection configurations.
- **Robust Connection Pooling**: Manages a pool of connections for high-performance applications, with lazy initialization.
- **Safe and Simple Transactions**: Provides a high-level `pool.transaction()` context manager to ensure atomic operations.
- **Production-Ready**: A background health monitor automatically closes and replaces stale, idle, and aged connections, ensuring long-term stability.
- **Fully Type-Hinted**: Clean, modern, and fully type-hinted code.

## Installation

You can install `aiosqlitepool` directly from PyPI:

```bash
pip install aiosqlitepool
```

## Quickstart

The easiest way to use the pool is by providing a database path.

```python
import asyncio
from aiosqlitepool import DatabaseSession

DB_PATH = "my_app.db"

async def main():
    pool = DatabaseSession(database_path=DB_PATH)

    # Use the transaction manager to ensure atomic operations
    async with pool.transaction() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS users (name TEXT)")
        await conn.execute("INSERT INTO users VALUES ('Alice')")

    # Fetch the data
    async with pool.connection() as conn:
        cursor = await conn.execute("SELECT name FROM users")
        row = await cursor.fetchone()
        print(f"Found user: {row[0]}")

    await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
```

## Advanced Usage: Connection Factory

For advanced use cases, such as setting a `row_factory` or using a different `aiosqlite`-compatible library, you can provide a `conn_factory`.

```python
import asyncio
import aiosqlite
from aiosqlitepool import DatabaseSession

DB_PATH = "my_app.db"

# 1. Define your custom factory
async def get_row_factory_connection():
    conn = await aiosqlite.connect(DB_PATH)
    conn.row_factory = aiosqlite.Row
    return conn

async def main():
    # 2. Pass the factory to the pool
    pool = DatabaseSession(conn_factory=get_row_factory_connection)

    async with pool.transaction() as conn:
        await conn.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER, name TEXT)")
        await conn.execute("INSERT INTO users VALUES (1, 'Bob')")

    async with pool.connection() as conn:
        cursor = await conn.execute("SELECT id, name FROM users WHERE id = 1")
        user = await cursor.fetchone()
        # Now you can access columns by name!
        print(f"Found user: id={user['id']}, name={user['name']}")

    await pool.close()

if __name__ == "__main__":
    asyncio.run(main())
```

## Configuration

The `DatabaseSession` can be configured with the following parameters:

- `database_path` (str, optional): The path to the SQLite database file.
- `conn_factory` (Callable, optional): An async function that returns a connection object.
- `pool_size` (int): The maximum number of connections in the pool. Defaults to `10`.
- `timeout` (float): Seconds to wait for a connection before raising a `RuntimeError`. Defaults to `30.0`.
- `max_connection_age` (float): Maximum age in seconds for any connection before it's replaced. Defaults to `3600` (1 hour).
- `max_idle_time` (float): Maximum time in seconds a connection can be idle in the pool before being closed. Defaults to `600` (10 minutes).

You must provide either `database_path` or `conn_factory`.
