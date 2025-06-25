import unittest
import asyncio
from typing import Any, Awaitable, Callable

import logging

from aiosqlitepool.manager import ConnectionManager
from aiosqlitepool.connection import PoolConnection


# --- Fake Implementations for Testing ---


class FakeConnection:
    """A test-friendly, simple implementation of the Connection protocol."""

    def __init__(self, should_fail: bool = False):
        self._connection = object()
        self.should_fail = should_fail
        self.closed = False
        self.rolled_back = False
        self.executions = 0

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        self.executions += 1
        if self.should_fail:
            raise Exception("Simulated connection failure")
        await asyncio.sleep(0)

    async def rollback(self) -> None:
        if self.should_fail:
            raise Exception("Simulated connection failure")
        self.rolled_back = True
        await asyncio.sleep(0)

    async def close(self) -> None:
        if self.should_fail:
            raise Exception("Simulated connection failure")
        self.closed = True
        self._connection = None
        await asyncio.sleep(0)


class FakeConnectionFactory:
    """A test-friendly factory for creating FakeConnection instances."""

    def __init__(self, should_fail: bool = False):
        self.should_fail = should_fail
        self.create_count = 0
        self.last_connection = None

    async def __call__(self) -> FakeConnection:
        self.create_count += 1
        if self.should_fail:
            raise Exception("Simulated factory failure")
        self.last_connection = FakeConnection()
        await asyncio.sleep(0)
        return self.last_connection


# --- Test Cases ---


class TestConnectionManager(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.factory = FakeConnectionFactory()
        self.manager = ConnectionManager(connection_factory=self.factory)

    async def test_create_and_wrap_connection(self):
        """
        Test that the manager uses the factory to create and wrap a new connection.
        """
        pool_conn = await self.manager.create()

        self.assertIsInstance(pool_conn, PoolConnection)
        self.assertIsInstance(pool_conn.raw_connection, FakeConnection)
        self.assertEqual(self.factory.create_count, 1)
        self.assertIs(pool_conn.raw_connection, self.factory.last_connection)

    async def test_close_connection_succeeds(self):
        """
        Test that the manager correctly closes a connection.
        """
        raw_conn = FakeConnection()
        pool_conn = PoolConnection(raw_conn, "conn_1")

        await self.manager.close(pool_conn)

        self.assertTrue(raw_conn.closed)

    async def test_health_check_alive(self):
        """
        Test that a healthy connection passes the health check.
        """
        raw_conn = FakeConnection()
        pool_conn = PoolConnection(raw_conn, "conn_1")

        self.assertTrue(await self.manager.is_alive(pool_conn))
        self.assertEqual(raw_conn.executions, 1)

    async def test_health_check_dead(self):
        """
        Test that a dead connection fails the health check.
        """
        raw_conn = FakeConnection(should_fail=True)
        pool_conn = PoolConnection(raw_conn, "conn_1")

        self.assertFalse(await self.manager.is_alive(pool_conn))

    async def test_reset_connection_always_rolls_back(self):
        """
        Test that reset() always attempts to roll back a transaction.
        """
        raw_conn = FakeConnection()
        pool_conn = PoolConnection(raw_conn, "conn_1")

        self.assertTrue(await self.manager.reset(pool_conn))

        self.assertTrue(raw_conn.rolled_back)


# --- Mocks for Non-Compliant Connections ---


class SyncConnection:
    """A connection with synchronous methods, violating the protocol."""

    def execute(self, *args, **kwargs):
        return "Not async"

    def rollback(self):
        pass

    def close(self):
        pass


class IncompleteConnection:
    """A connection that is missing the 'close' method."""

    async def execute(self, *args, **kwargs):
        return "Partial Success"

    async def rollback(self):
        pass


class TestManagerWithBadConnections(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Suppress expected warning/error logs during these tests
        logging.disable(logging.WARNING)
        self.manager = ConnectionManager(connection_factory=FakeConnectionFactory())

    def tearDown(self):
        logging.disable(logging.NOTSET)

    async def test_is_alive_with_sync_connection(self):
        """
        Test that is_alive() handles a non-awaitable execute method gracefully.
        """
        pool_conn = PoolConnection(SyncConnection(), "conn_1")
        self.assertFalse(await self.manager.is_alive(pool_conn))

    async def test_is_alive_with_incomplete_connection(self):
        """
        Test that is_alive() handles a missing execute method gracefully.
        """
        # This connection is missing .execute()
        pool_conn = PoolConnection(object(), "conn_1")
        self.assertFalse(await self.manager.is_alive(pool_conn))

    async def test_reset_with_sync_connection(self):
        """
        Test that reset() raises TypeError for non-awaitable methods.
        """
        pool_conn = PoolConnection(SyncConnection(), "conn_1")
        with self.assertRaises(TypeError):
            await self.manager.reset(pool_conn)

    async def test_close_with_sync_connection(self):
        """
        Test that close() raises TypeError for non-awaitable methods.
        """
        pool_conn = PoolConnection(SyncConnection(), "conn_1")
        with self.assertRaises(TypeError):
            await self.manager.close(pool_conn)

    async def test_close_with_incomplete_connection(self):
        """
        Test that close() raises AttributeError for missing methods.
        """
        pool_conn = PoolConnection(IncompleteConnection(), "conn_1")
        with self.assertRaises(AttributeError):
            await self.manager.close(pool_conn)
