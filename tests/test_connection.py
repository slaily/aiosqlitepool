import unittest
import asyncio
import time
from unittest.mock import AsyncMock

from aiosqlitepool.connection import PoolConnection
from aiosqlitepool.protocols import Connection


class TestPoolConnection(unittest.IsolatedAsyncioTestCase):
    async def test_creation_and_attributes(self):
        """
        Test that a PoolConnection is created with the correct initial attributes.
        """
        mock_conn = AsyncMock(spec=Connection)
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
        mock_conn = AsyncMock(spec=Connection)
        pool_conn = PoolConnection(connection=mock_conn, connection_id="conn_1")

        # Simulate time passing
        await asyncio.sleep(0.1)

        self.assertGreater(pool_conn.age, 0.09)
        self.assertLess(pool_conn.age, 0.15)
