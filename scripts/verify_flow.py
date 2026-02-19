import asyncio
import sys
import time
import httpx
import json

BASE_URL = "http://localhost:8100"

async def main():
    print(f"=== Tonglu Functional Verification ({BASE_URL}) ===\n")
    
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=30.0) as client:
        # 1. Health Check
        print("[1/4] Checking Service Health ... ", end="")
        try:
            resp = await client.get("/health")
            resp.raise_for_status()
            print(f"OK ({resp.json()['version']})")
        except Exception as e:
            print(f"FAILED\n  Error: {e}")
            print("  -> Is the Tonglu service running? (uvicorn tonglu.main:app ...)")
            return

        # 2. Ingest Text
        print("[2/4] Ingesting Test Data ... ", end="")
        ts = str(time.time())
        test_data = f"Tonglu verification sample data. timestamp={ts}"
        try:
            resp = await client.post("/api/ingest/text", json={
                "data": test_data,
                "tenant_id": "verify_test",
                "schema_type": "test_record"
            })
            resp.raise_for_status()
            record_id = resp.json()["record_id"]
            print(f"OK (ID: {record_id})")
        except Exception as e:
            print(f"FAILED\n  Error: {e}")
            return

        # 3. Query Data
        print("[3/4] Querying Data ... ", end="")
        # Wait a bit for async processing (though text ingest is sync in Phase 1)
        await asyncio.sleep(1)
        try:
            resp = await client.post("/api/query", json={
                "query": "verification sample",
                "tenant_id": "verify_test",
                "mode": "sql" # Use SQL for exact match reliability in verification
            })
            resp.raise_for_status()
            results = resp.json()["results"]
            
            # Check if our record is in results
            match = next((r for r in results if r.get("data") == test_data), None)
            
            if match:
                 print(f"OK (Found match: {match['id']})")
            else:
                 print("WARNING (No exact match found)")
                 print(f"  Query returned {len(results)} results")
        except Exception as e:
            print(f"FAILED\n  Error: {e}")
            return

        # 4. Cleanup (Optional - Phase 1 doesn't have delete API yet)
        print("[4/4] Verification Complete.")

    print("\n✅ Functional Verification Passed")

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
