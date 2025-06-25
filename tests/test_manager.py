import unittest
import asyncio
import aiosqlite
from unittest.mock import MagicMock, AsyncMock

from aiosqlitepool.factory import ConnectionFactory
from aiosqlitepool.manager import ConnectionManager
from aiosqlitepool.connection import PoolConnection


class TestConnectionManager(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.mock_factory = AsyncMock(spec=ConnectionFactory)
        self.manager = ConnectionManager(factory=self.mock_factory)

    async def test_create_and_wrap_connection(self):
        """
        Test that the manager uses the factory to create and wrap a new connection.
        """
        mock_raw_conn = AsyncMock(spec=aiosqlite.Connection)
        self.mock_factory.create.return_value = mock_raw_conn

        pool_conn = await self.manager.create()

        self.assertIsInstance(pool_conn, PoolConnection)
        self.assertIs(pool_conn.raw_connection, mock_raw_conn)
        self.mock_factory.create.assert_awaited_once()

    async def test_close_connection_succeeds(self):
        """
        Test that the manager correctly closes a connection.
        """
        mock_raw_conn = AsyncMock(spec=aiosqlite.Connection)
        pool_conn = PoolConnection(mock_raw_conn, "conn_1")

        await self.manager.close(pool_conn)

        mock_raw_conn.close.assert_awaited_once()

    async def test_health_check_alive(self):
        """
        Test that a healthy connection passes the health check.
        """
        mock_raw_conn = MagicMock(spec=aiosqlite.Connection)
        mock_cursor = AsyncMock()
        mock_raw_conn.execute = AsyncMock(return_value=mock_cursor)
        pool_conn = PoolConnection(mock_raw_conn, "conn_1")

        self.assertTrue(await self.manager.is_alive(pool_conn))
        mock_raw_conn.execute.assert_awaited_once_with("SELECT 1")
        mock_cursor.fetchone.assert_awaited_once()

    async def test_health_check_dead(self):
        """
        Test that a dead connection fails the health check.
        """
        mock_raw_conn = MagicMock(spec=aiosqlite.Connection)
        mock_raw_conn.execute = AsyncMock(
            side_effect=aiosqlite.Error("Connection closed")
        )
        pool_conn = PoolConnection(mock_raw_conn, "conn_1")

        self.assertFalse(await self.manager.is_alive(pool_conn))

    async def test_reset_connection_with_transaction(self):
        """
        Test that reset() rolls back an open transaction.
        """
        mock_raw_conn = MagicMock(spec=aiosqlite.Connection)
        mock_raw_conn.in_transaction = True
        mock_raw_conn.rollback = AsyncMock()
        pool_conn = PoolConnection(mock_raw_conn, "conn_1")

        await self.manager.reset(pool_conn)

        mock_raw_conn.rollback.assert_awaited_once()

    async def test_reset_connection_without_transaction(self):
        """
        Test that reset() does nothing if no transaction is open.
        """
        mock_raw_conn = MagicMock(spec=aiosqlite.Connection)
        mock_raw_conn.in_transaction = False
        mock_raw_conn.rollback = AsyncMock()
        pool_conn = PoolConnection(mock_raw_conn, "conn_1")

        await self.manager.reset(pool_conn)

        mock_raw_conn.rollback.assert_not_awaited()
