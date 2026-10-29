"""Create an owner and print its API key.

Usage:  python -m scripts.create_owner "Alice"
        docker compose exec api python -m scripts.create_owner "Alice"
"""

import asyncio
import sys

from app.auth import generate_api_key, hash_api_key
from app.db import SessionLocal, engine
from app.models import Owner


async def main(name: str) -> None:
    api_key = generate_api_key()
    async with SessionLocal() as session:
        owner = Owner(name=name, api_key_hash=hash_api_key(api_key))
        session.add(owner)
        await session.commit()
    await engine.dispose()
    print(f"owner_id: {owner.id}")
    print(f"api_key:  {api_key}   (shown once — store it now)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1]))
