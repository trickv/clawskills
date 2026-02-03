"""API key authentication."""

import hashlib
import secrets
from datetime import datetime
from typing import Optional
from fastapi import Header, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from .database import get_db
from .models import APIKey


def hash_key(key: str) -> str:
    """Hash an API key using SHA-256."""
    return hashlib.sha256(key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str]:
    """Generate a new API key. Returns (raw_key, hashed_key)."""
    raw_key = f"csk_{secrets.token_urlsafe(32)}"
    hashed = hash_key(raw_key)
    return raw_key, hashed


async def get_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> APIKey:
    """Validate API key and return the key record."""
    if not x_api_key:
        raise HTTPException(
            status_code=401,
            detail="Missing X-API-Key header"
        )
    
    key_hash = hash_key(x_api_key)
    result = await db.execute(
        select(APIKey).where(
            APIKey.key_hash == key_hash,
            APIKey.is_active == True
        )
    )
    api_key = result.scalar_one_or_none()
    
    if not api_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid or inactive API key"
        )
    
    # Update last_used
    api_key.last_used = datetime.utcnow()
    await db.commit()
    
    return api_key


async def optional_api_key(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    db: AsyncSession = Depends(get_db),
) -> Optional[APIKey]:
    """Optionally validate API key if provided."""
    if not x_api_key:
        return None
    
    try:
        return await get_api_key(x_api_key, db)
    except HTTPException:
        return None
