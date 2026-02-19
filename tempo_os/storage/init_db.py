# Copyright (c) 2026 TempoOS Contributors. All Rights Reserved.

"""
Database Initialization — Create tables from ORM metadata.
"""

import asyncio

from tempo_os.storage.database import create_all_tables, close_db

# Ensure models are imported so Base.metadata knows about them
import tempo_os.storage.models  # noqa: F401


async def main():
    """Create all platform tables."""
    print("[init_db] Creating tables...")
    await create_all_tables()
    print("[init_db] Done.")
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
