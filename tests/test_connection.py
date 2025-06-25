import time
import unittest

from typing import Any
from asyncio import sleep

from aiosqlitepool.connection import PoolConnection


class SQLiteConnection:
    def __init__(self):
        self._connection = object()
        self.closed = False
        self.rolled_back = False

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        pass

    async def rollback(self) -> None:
        self.rolled_back = True
        await sleep(0)

    async def close(self) -> None:
        self.closed = True
        self._connection = None
        await sleep(0)


class TestPoolConnection(unittest.IsolatedAsyncioTestCase):
    async def test_creation_and_attributes(self):
        """
        Test that a PoolConnection is created with the correct initial attributes.
        """
        mock_conn = SQLiteConnection()
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
        mock_conn = SQLiteConnection()
        pool_conn = PoolConnection(connection=mock_conn, connection_id="conn_1")

        # Simulate time passing
        await sleep(0.1)

        self.assertGreater(pool_conn.age, 0.09)
        self.assertLess(pool_conn.age, 0.15)
