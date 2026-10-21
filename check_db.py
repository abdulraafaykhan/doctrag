"""Run once to verify the Atlas connection and create the vector index:

    uv run python check_db.py
"""
import asyncio

from app.db import close_mongo, connect_to_mongo, ensure_indexes, get_db, vector_index_status


async def main() -> None:
    print("Connecting to MongoDB Atlas...")
    await connect_to_mongo()
    print("  connected (ping OK)")

    await ensure_indexes()
    print("  collections:", sorted(await get_db().list_collection_names()))

    for _ in range(12):  # poll up to ~1 minute
        status = await vector_index_status()
        print("  vector index status:", status)
        if status == "READY":
            break
        await asyncio.sleep(5)

    await close_mongo()


if __name__ == "__main__":
    asyncio.run(main())