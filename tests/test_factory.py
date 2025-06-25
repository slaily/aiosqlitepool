import unittest
import asyncio
import os
import aiosqlite
from unittest.mock import AsyncMock

from aiosqlitepool.factory import ConnectionFactory

DB_PATH = "test_factory.db"


class TestConnectionFactory(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Ensure the test database file does not exist
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)

    def tearDown(self):
        # Clean up the test database file after tests
        if os.path.exists(DB_PATH):
            os.remove(DB_PATH)

    async def test_create_with_path(self):
        """
        Test successful connection creation using a database_path.
        """
        factory = ConnectionFactory(database_path=DB_PATH)
        conn = await factory.create()

        self.assertIsInstance(conn, aiosqlite.Connection)
        await conn.close()

    async def test_create_with_factory(self):
        """
        Test successful connection creation using a custom conn_factory.
        """
        # Define a custom async factory function
        custom_conn_mock = AsyncMock(spec=aiosqlite.Connection)

        async def my_factory() -> aiosqlite.Connection:
            return custom_conn_mock

        factory = ConnectionFactory(conn_factory=my_factory)
        conn = await factory.create()

        self.assertIs(conn, custom_conn_mock)

    def test_creation_fails_with_no_source(self):
        """
        Test that a ValueError is raised if no connection source is provided.
        """
        with self.assertRaisesRegex(
            ValueError, "Either 'database_path' or 'conn_factory' must be provided"
        ):
            ConnectionFactory()
