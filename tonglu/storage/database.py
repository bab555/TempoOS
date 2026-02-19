# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Database Connection Manager — Async SQLAlchemy engine and session factory.
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from tonglu.storage.models import Base

logger = logging.getLogger("tonglu.database")


class Database:
    """
    Manages the async SQLAlchemy engine and session factory.

    Usage:
        db = Database("postgresql+asyncpg://...")
        await db.init()       # Create tables (dev) or verify connection
        session = db.session() # Get a new AsyncSession
        await db.close()      # Dispose engine on shutdown
    """

    def __init__(self, database_url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(
            database_url,
            pool_size=10,
            max_overflow=20,
            echo=False,
        )
        self.session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    def session(self) -> AsyncSession:
        """Create a new async session."""
        return self.session_factory()

    async def init(self) -> None:
        """
        Create all tables if they don't exist.

        NOTE: For production, use Alembic migrations instead.
        """
        logger.info("Initializing Tonglu database tables...")
        async with self.engine.begin() as conn:
            # pgvector 扩展必须在建表前启用，否则 VECTOR 类型不可用
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Tonglu database tables ready.")

    async def close(self) -> None:
        """Dispose the engine and release all connections."""
        logger.info("Closing Tonglu database connections...")
        await self.engine.dispose()
