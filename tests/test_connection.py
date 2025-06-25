import unittest
import asyncio
import time
import aiosqlite
from unittest.mock import MagicMock, AsyncMock

from aiosqlitepool.connection import PoolConnection


class TestPoolConnection(unittest.IsolatedAsyncioTestCase):
    async def test_creation_and_attributes(self):
        """
        Test that a PoolConnection is created with the correct initial attributes.
        """
        mock_conn = AsyncMock(spec=aiosqlite.Connection)
        conn_id = "conn_1"

        pool_conn = PoolConnection(connection=mock_conn, connection_id=conn_id)

        self.assertIs(pool_conn.raw_connection, mock_conn)
        self.assertEqual(pool_conn.id, conn_id)
        self.assertIsInstance(pool_conn.created_at, float)
        self.assertAlmostEqual(pool_conn.created_at, time.time(), places=1)

    async def test_age_property(self):
        """
        Test that the 'age' property correctly calculates the connection's age.
        """
        mock_conn = AsyncMock(spec=aiosqlite.Connection)
        pool_conn = PoolConnection(connection=mock_conn, connection_id="conn_1")

        # Simulate time passing
        await asyncio.sleep(0.1)

        self.assertGreater(pool_conn.age, 0.09)
        self.assertLess(pool_conn.age, 0.15)

    async def test_is_closed_property(self):
        """
        Test that 'is_closed' reflects the state of the raw connection.
        """
        # Mock a connection that appears open
        mock_open_conn = MagicMock(spec=aiosqlite.Connection)
        mock_open_conn._connection = None  # aiosqlite uses this to check if closed

        pool_open_conn = PoolConnection(
            connection=mock_open_conn, connection_id="conn_1"
        )
        self.assertFalse(pool_open_conn.is_closed)

        # Mock a connection that appears closed
        mock_closed_conn = MagicMock(spec=aiosqlite.Connection)
        # Setting _connection to a truthy value makes it appear closed
        mock_closed_conn._connection = True

        pool_closed_conn = PoolConnection(
            connection=mock_closed_conn, connection_id="conn_2"
        )
        self.assertTrue(pool_closed_conn.is_closed)
