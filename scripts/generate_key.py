#!/usr/bin/env python3
"""Generate API keys for ClawdSkills."""

import argparse
import asyncio
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.auth import generate_api_key
from app.database import init_db, async_session_maker
from app.crud import create_api_key


async def main():
    parser = argparse.ArgumentParser(description="Generate API keys for ClawdSkills")
    parser.add_argument("--label", "-l", help="Label for the API key", default=None)
    parser.add_argument("--init-db", action="store_true", help="Initialize database first")
    args = parser.parse_args()
    
    # Initialize database if requested
    if args.init_db:
        print("Initializing database...")
        await init_db()
        print("Database initialized.")
    
    # Generate key
    raw_key, key_hash = generate_api_key()
    
    # Save to database
    async with async_session_maker() as db:
        await create_api_key(db, key_hash, args.label)
    
    print("\n" + "=" * 60)
    print("API KEY GENERATED")
    print("=" * 60)
    print(f"\nKey: {raw_key}")
    print(f"Label: {args.label or '(none)'}")
    print("\n⚠️  SAVE THIS KEY NOW - it cannot be retrieved later!")
    print("=" * 60 + "\n")
    
    return raw_key


if __name__ == "__main__":
    asyncio.run(main())
