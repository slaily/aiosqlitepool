"""A robust, high-performance asynchronous connection pool for SQLite."""

from .client import SQLiteConnectionPool
from .exceptions import (
    PoolClosedError,
    PoolConnectionAcquireTimeoutError,
)

__all__ = [
    "SQLiteConnectionPool",
    "PoolClosedError",
    "PoolConnectionAcquireTimeoutError",
]

__version__ = "1.0.0.beta"
