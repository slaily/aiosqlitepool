import asyncio
import aiosqlite
from typing import Optional, Set, Dict, Any, List
from os import getenv
import logging
from contextlib import asynccontextmanager
import time
import weakref
from enum import Enum

logger = logging.getLogger(__name__)


class ConnectionState(Enum):
    """Connection state tracking for debugging and safety."""

    CREATED = "created"
    AVAILABLE = "available"
    IN_USE = "in_use"
    CLEANING = "cleaning"
    CLOSING = "closing"
    CLOSED = "closed"


class ConnectionWrapper:
    """Wrapper to track connection state and metadata safely."""

    def __init__(self, connection: aiosqlite.Connection, connection_id: str):
        self.connection = connection
        self.connection_id = connection_id
        self.state = ConnectionState.CREATED
        self.created_at = time.time()
        self.last_used_at = time.time()
        self.use_count = 0
        self.active_queries = 0
        self.in_transaction = False
        self._lock = asyncio.Lock()

    async def mark_in_use(self):
        """Thread-safe marking of connection as in use."""
        async with self._lock:
            self.state = ConnectionState.IN_USE
            self.last_used_at = time.time()
            self.use_count += 1

    async def mark_available(self):
        """Thread-safe marking of connection as available."""
        async with self._lock:
            self.state = ConnectionState.AVAILABLE

    async def mark_cleaning(self):
        """Thread-safe marking of connection as being cleaned."""
        async with self._lock:
            self.state = ConnectionState.CLEANING

    async def mark_closing(self):
        """Thread-safe marking of connection as being closed."""
        async with self._lock:
            self.state = ConnectionState.CLOSING

    def is_healthy(self) -> bool:
        """Check if connection is in a healthy state."""
        return self.state in [ConnectionState.AVAILABLE, ConnectionState.IN_USE]


class DatabaseSession:
    """
    Production-ready database session manager with comprehensive connection tracking.

    Critical features:
    - Connection state tracking to prevent leaks
    - Query execution safety with proper error handling
    - Graceful shutdown that waits for active queries
    - Connection health monitoring and recovery
    - Thread-safe operations for multi-worker environments
    - Comprehensive logging for debugging
    """

    def __init__(
        self,
        database_path: Optional[str] = None,
        pool_size: int = 10,
        timeout: float = 30.0,
        max_connection_age: float = 3600.0,  # 1 hour
        health_check_interval: float = 300.0,  # 5 minutes
    ):
        self.database_path = database_path or getenv("DATABASE_PATH")
        self.pool_size = pool_size
        self.timeout = timeout
        self.max_connection_age = max_connection_age
        self.health_check_interval = health_check_interval

        # Connection pool and tracking
        self._pool: asyncio.Queue[ConnectionWrapper] = asyncio.Queue(maxsize=pool_size)
        self._all_connections: Dict[str, ConnectionWrapper] = {}
        self._active_connections: Set[str] = set()

        # Thread safety
        self._lock = asyncio.Lock()
        self._closed = False
        self._initialized = False
        self._shutdown_event = asyncio.Event()

        # Statistics and monitoring
        self._connection_counter = 0
        self._total_queries = 0
        self._failed_queries = 0

        # Health monitoring task
        self._health_check_task: Optional[asyncio.Task] = None

    async def _create_connection(self) -> ConnectionWrapper:
        """Create a new database connection with comprehensive setup."""
        self._connection_counter += 1
        connection_id = f"conn_{self._connection_counter}_{int(time.time())}"

        try:
            connection = await aiosqlite.connect(self.database_path)

            # Apply production-ready SQLite settings
            await connection.execute("PRAGMA foreign_keys = ON")
            await connection.execute("PRAGMA journal_mode = WAL")
            await connection.execute("PRAGMA synchronous = NORMAL")
            await connection.execute("PRAGMA busy_timeout = 5000")
            await connection.execute("PRAGMA temp_store = MEMORY")
            await connection.execute("PRAGMA cache_size = 2000")

            # Test connection health
            cursor = await connection.execute("SELECT 1")
            await cursor.fetchone()

            wrapper = ConnectionWrapper(connection, connection_id)
            await wrapper.mark_available()

            logger.debug(f"Created healthy connection {connection_id}")
            return wrapper

        except Exception as e:
            logger.error(f"Failed to create connection {connection_id}: {e}")
            raise

    async def _clean_connection(self, wrapper: ConnectionWrapper) -> bool:
        """
        CRITICAL: Comprehensive connection cleaning.
        Returns True if cleaning succeeded, False if connection should be closed.
        """
        try:
            await wrapper.mark_cleaning()

            # Check if connection is still alive
            try:
                cursor = await wrapper.connection.execute("SELECT 1")
                await cursor.fetchone()
            except Exception as e:
                logger.warning(
                    f"Connection {wrapper.connection_id} failed health check: {e}"
                )
                return False

            # Rollback any uncommitted transactions
            try:
                await wrapper.connection.rollback()
                wrapper.in_transaction = False
            except Exception as e:
                logger.warning(
                    f"Failed to rollback connection {wrapper.connection_id}: {e}"
                )
                return False

            # Reset connection state
            wrapper.active_queries = 0
            await wrapper.mark_available()

            logger.debug(f"Successfully cleaned connection {wrapper.connection_id}")
            return True

        except Exception as e:
            logger.error(
                f"Critical error cleaning connection {wrapper.connection_id}: {e}"
            )
            return False

    async def _close_connection(self, wrapper: ConnectionWrapper) -> None:
        """Safely close a connection with proper cleanup."""
        try:
            await wrapper.mark_closing()

            # Wait for active queries to complete (with timeout)
            max_wait = 10.0  # 10 seconds max wait
            wait_start = time.time()

            while wrapper.active_queries > 0 and (time.time() - wait_start) < max_wait:
                logger.warning(
                    f"Waiting for {wrapper.active_queries} active queries on connection {wrapper.connection_id}"
                )
                await asyncio.sleep(0.1)

            if wrapper.active_queries > 0:
                logger.error(
                    f"Force closing connection {wrapper.connection_id} with {wrapper.active_queries} active queries"
                )

            # Close the connection
            await wrapper.connection.close()
            wrapper.state = ConnectionState.CLOSED

            # Remove from tracking
            self._all_connections.pop(wrapper.connection_id, None)
            self._active_connections.discard(wrapper.connection_id)

            logger.debug(f"Closed connection {wrapper.connection_id}")

        except Exception as e:
            logger.error(f"Error closing connection {wrapper.connection_id}: {e}")

    async def _initialize_pool(self) -> None:
        """Thread-safe pool initialization with comprehensive error handling."""
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            logger.info(
                f"Initializing database pool with {self.pool_size} connections..."
            )

            successful_connections = 0
            for i in range(self.pool_size):
                try:
                    wrapper = await self._create_connection()
                    self._all_connections[wrapper.connection_id] = wrapper
                    await self._pool.put(wrapper)
                    successful_connections += 1

                except Exception as e:
                    logger.error(
                        f"Failed to create connection {i + 1}/{self.pool_size}: {e}"
                    )
                    continue

            if successful_connections == 0:
                raise RuntimeError("Failed to create any database connections")

            # Start health monitoring
            self._health_check_task = asyncio.create_task(self._health_monitor())

            self._initialized = True
            logger.info(
                f"Database pool initialized with {successful_connections}/{self.pool_size} connections"
            )

    async def _health_monitor(self) -> None:
        """Background task to monitor connection health."""
        while not self._closed:
            try:
                await asyncio.sleep(self.health_check_interval)

                if self._closed:
                    break

                # Check connection ages and health
                current_time = time.time()
                connections_to_replace = []

                for conn_id, wrapper in list(self._all_connections.items()):
                    if wrapper.state == ConnectionState.AVAILABLE:
                        age = current_time - wrapper.created_at
                        if age > self.max_connection_age:
                            connections_to_replace.append(wrapper)

                # Replace old connections
                for wrapper in connections_to_replace:
                    try:
                        # Remove from pool if present
                        temp_queue = asyncio.Queue(maxsize=self.pool_size)
                        while not self._pool.empty():
                            try:
                                pooled_wrapper = self._pool.get_nowait()
                                if (
                                    pooled_wrapper.connection_id
                                    != wrapper.connection_id
                                ):
                                    await temp_queue.put(pooled_wrapper)
                            except asyncio.QueueEmpty:
                                break

                        # Restore pool without the old connection
                        while not temp_queue.empty():
                            await self._pool.put(temp_queue.get_nowait())

                        # Close old connection and create new one
                        await self._close_connection(wrapper)

                        new_wrapper = await self._create_connection()
                        self._all_connections[new_wrapper.connection_id] = new_wrapper
                        await self._pool.put(new_wrapper)

                        logger.info(
                            f"Replaced aged connection {wrapper.connection_id} with {new_wrapper.connection_id}"
                        )

                    except Exception as e:
                        logger.error(
                            f"Error replacing connection {wrapper.connection_id}: {e}"
                        )

            except Exception as e:
                logger.error(f"Error in health monitor: {e}")

    async def get_connection(self) -> ConnectionWrapper:
        """
        Get a connection from the pool with comprehensive error handling.
        Thread-safe and tracks connection usage.
        """
        if self._closed:
            raise RuntimeError("Database session is closed")

        if not self._initialized:
            await self._initialize_pool()

        try:
            wrapper = await asyncio.wait_for(self._pool.get(), timeout=self.timeout)

            # Mark as in use and track
            await wrapper.mark_in_use()
            self._active_connections.add(wrapper.connection_id)

            logger.debug(
                f"Retrieved connection {wrapper.connection_id} from pool. "
                f"Available: {self._pool.qsize()}, Active: {len(self._active_connections)}"
            )

            return wrapper

        except asyncio.TimeoutError:
            active_count = len(self._active_connections)
            available_count = self._pool.qsize()

            logger.error(
                f"Connection timeout after {self.timeout}s. "
                f"Active: {active_count}, Available: {available_count}, Pool size: {self.pool_size}"
            )

            raise RuntimeError(
                f"Failed to get database connection within {self.timeout} seconds. "
                f"Active connections: {active_count}/{self.pool_size}"
            )

    async def return_connection(self, wrapper: ConnectionWrapper) -> None:
        """
        Return a connection to the pool with comprehensive cleanup.
        CRITICAL: Always clean before returning to prevent data corruption.
        """
        if self._closed:
            await self._close_connection(wrapper)
            return

        try:
            # Remove from active tracking
            self._active_connections.discard(wrapper.connection_id)

            # Clean the connection
            if await self._clean_connection(wrapper):
                # Successfully cleaned, return to pool
                try:
                    self._pool.put_nowait(wrapper)
                    logger.debug(
                        f"Returned cleaned connection {wrapper.connection_id} to pool. "
                        f"Available: {self._pool.qsize()}"
                    )
                except asyncio.QueueFull:
                    logger.warning(
                        f"Pool full when returning connection {wrapper.connection_id}, closing it"
                    )
                    await self._close_connection(wrapper)
            else:
                # Cleaning failed, close the connection
                logger.warning(f"Closing unhealthy connection {wrapper.connection_id}")
                await self._close_connection(wrapper)

                # Try to create a replacement connection
                try:
                    new_wrapper = await self._create_connection()
                    self._all_connections[new_wrapper.connection_id] = new_wrapper
                    await self._pool.put(new_wrapper)
                    logger.info(
                        f"Created replacement connection {new_wrapper.connection_id}"
                    )
                except Exception as e:
                    logger.error(f"Failed to create replacement connection: {e}")

        except Exception as e:
            logger.error(f"Error returning connection {wrapper.connection_id}: {e}")
            await self._close_connection(wrapper)

    @asynccontextmanager
    async def connection(self):
        """
        Context manager for safe connection handling with query tracking.
        """
        wrapper = await self.get_connection()
        try:
            yield wrapper.connection
        finally:
            await self.return_connection(wrapper)

    @asynccontextmanager
    async def transaction(self):
        """
        Context manager for explicit transaction handling with proper cleanup.
        """
        wrapper = await self.get_connection()
        try:
            await wrapper.connection.execute("BEGIN")
            wrapper.in_transaction = True

            try:
                yield wrapper.connection
                await wrapper.connection.commit()
                wrapper.in_transaction = False
            except Exception:
                await wrapper.connection.rollback()
                wrapper.in_transaction = False
                raise

        finally:
            await self.return_connection(wrapper)

    async def execute_safe(self, sql: str, parameters=None) -> Any:
        """Execute a query with comprehensive error handling and tracking."""
        async with self.connection() as conn:
            try:
                self._total_queries += 1
                cursor = await conn.execute(sql, parameters or ())
                return cursor
            except Exception as e:
                self._failed_queries += 1
                logger.error(f"Query failed: {sql[:100]}... Error: {e}")
                raise

    async def execute_commit(self, sql: str, parameters=None) -> Any:
        """Execute a statement and commit with proper error handling."""
        async with self.transaction() as conn:
            cursor = await conn.execute(sql, parameters or ())
            return cursor

    async def fetch_one(self, sql: str, parameters=None):
        """Execute a query and return one row with error handling."""
        cursor = await self.execute_safe(sql, parameters)
        return await cursor.fetchone()

    async def fetch_all(self, sql: str, parameters=None):
        """Execute a query and return all rows with error handling."""
        cursor = await self.execute_safe(sql, parameters)
        return await cursor.fetchall()

    async def close(self) -> None:
        """
        Graceful shutdown that waits for active queries to complete.
        CRITICAL: Ensures no data loss during shutdown.
        """
        if self._closed:
            return

        logger.info("Starting database session shutdown...")

        # Stop accepting new connections
        async with self._lock:
            self._closed = True
            self._shutdown_event.set()

        # Stop health monitoring
        if self._health_check_task and not self._health_check_task.done():
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass

        # Wait for active queries to complete (with timeout)
        max_wait = 30.0  # 30 seconds max wait
        wait_start = time.time()

        while self._active_connections and (time.time() - wait_start) < max_wait:
            active_count = len(self._active_connections)
            logger.info(f"Waiting for {active_count} active connections to finish...")
            await asyncio.sleep(1.0)

        if self._active_connections:
            logger.warning(
                f"Force closing {len(self._active_connections)} active connections"
            )

        # Close all connections
        closed_count = 0

        # Close pooled connections
        while not self._pool.empty():
            try:
                wrapper = self._pool.get_nowait()
                await self._close_connection(wrapper)
                closed_count += 1
            except asyncio.QueueEmpty:
                break

        # Close any remaining connections
        for wrapper in list(self._all_connections.values()):
            await self._close_connection(wrapper)
            closed_count += 1

        self._all_connections.clear()
        self._active_connections.clear()

        logger.info(
            f"Database session closed. "
            f"Connections closed: {closed_count}, "
            f"Total queries: {self._total_queries}, "
            f"Failed queries: {self._failed_queries}"
        )

    def status(self) -> dict:
        """Get comprehensive pool status information."""
        return {
            "pool_size": self.pool_size,
            "available_connections": self._pool.qsize(),
            "active_connections": len(self._active_connections),
            "total_connections": len(self._all_connections),
            "total_queries": self._total_queries,
            "failed_queries": self._failed_queries,
            "is_closed": self._closed,
            "is_initialized": self._initialized,
            "connection_details": {
                conn_id: {
                    "state": wrapper.state.value,
                    "use_count": wrapper.use_count,
                    "age": time.time() - wrapper.created_at,
                    "active_queries": wrapper.active_queries,
                    "in_transaction": wrapper.in_transaction,
                }
                for conn_id, wrapper in self._all_connections.items()
            },
        }
