import asyncio
import os
import sys
import importlib.util

# Add project root to sys.path
sys.path.append(os.getcwd())

def check_import(package_name):
    print(f"[Check] Import: {package_name} ... ", end="")
    if importlib.util.find_spec(package_name):
        print("OK")
        return True
    print("FAILED (pip install required)")
    return False

async def check_db(url):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text
    print(f"[Check] Database: {url.split('@')[-1]} ... ", end="")
    try:
        engine = create_async_engine(url)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        print("OK")
        return True
    except Exception as e:
        print(f"FAILED\n  Error: {e}")
        return False

async def check_redis(url):
    import redis.asyncio as aioredis
    print(f"[Check] Redis: {url} ... ", end="")
    try:
        r = aioredis.from_url(url)
        await r.ping()
        await r.close()
        print("OK")
        return True
    except Exception as e:
        print(f"FAILED\n  Error: {e}")
        return False

def check_api_key(key):
    print(f"[Check] DashScope API Key ... ", end="")
    if not key:
        print("MISSING (Required for LLM features)")
        return False
    if key.startswith("sk-"):
        print("OK (Format looks valid)")
        return True
    print("WARNING (Invalid format?)")
    return True

async def main():
    print("=== Tonglu Environment Verification ===\n")
    
    # 1. Check Dependencies
    pkgs = ["fastapi", "uvicorn", "python_multipart", "sqlalchemy", "asyncpg", "redis", "dashscope"]
    if not all(check_import(p) for p in pkgs):
        print("\n[FATAL] Missing dependencies. Run: pip install -r requirements.txt")
        return

    # 2. Load Settings
    try:
        from tonglu.config import TongluSettings
        settings = TongluSettings()
        print(f"[Info] Loaded Config: DB={settings.DATABASE_URL.split('@')[-1]}, Redis={settings.REDIS_URL}")
    except Exception as e:
        print(f"\n[FATAL] Configuration load failed: {e}")
        print("  -> Check your .env file format")
        return

    # 3. Infrastructure Checks
    db_ok = await check_db(settings.DATABASE_URL)
    redis_ok = await check_redis(settings.REDIS_URL)
    key_ok = check_api_key(settings.DASHSCOPE_API_KEY)

    print("\n=== Summary ===")
    if db_ok and redis_ok and key_ok:
        print("[OK] Environment Ready. You can now start the service:")
        print("     uvicorn tonglu.main:app --host 0.0.0.0 --port 8100 --reload")
    else:
        print("[WARN] Environment Issues Found")
        if not key_ok:
            print("   - Please set DASHSCOPE_API_KEY in .env")
        if not db_ok:
            print("   - Ensure PostgreSQL is running and DATABASE_URL is correct")
        if not redis_ok:
            print("   - Ensure Redis is running and REDIS_URL is correct")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
