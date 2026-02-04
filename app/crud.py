"""Database CRUD operations."""

import logging
from datetime import datetime
from typing import Optional
from collections import Counter
import httpx
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from .models import Solution, Vote, APIKey, SolutionStatus, VoteType
from .schemas import SolutionCreate, VoteCreate
from .auth import hash_key

logger = logging.getLogger(__name__)


class SkillURLValidationError(Exception):
    """Raised when a skill URL fails validation."""
    pass


async def validate_skill_url(url: str) -> tuple[bool, str, Optional[str]]:
    """
    Validate that a skill URL points to a valid skill file.
    
    Returns (is_valid, reason, sha256_hash).
    """
    import hashlib
    
    # Allowed content types for skill files
    ALLOWED_CONTENT_TYPES = [
        'text/plain',
        'text/markdown',
        'text/x-markdown',
        'text/html',  # GitHub renders markdown as HTML sometimes
        'application/octet-stream',  # Some servers don't set proper content-type
    ]
    
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(url, headers={
                'User-Agent': 'ClawSkills/1.0 (skill-validator)'
            })
            
            # Check status code
            if response.status_code != 200:
                reason = f"URL returned status {response.status_code}"
                logger.warning(f"Skill URL validation failed: {url} - {reason}")
                return False, reason, None
            
            # Check content type
            content_type = response.headers.get('content-type', '').lower().split(';')[0].strip()
            if content_type and content_type not in ALLOWED_CONTENT_TYPES:
                reason = f"Invalid content type: {content_type}"
                logger.warning(f"Skill URL validation failed: {url} - {reason}")
                return False, reason, None
            
            # Get full content for hashing
            content_bytes = response.content
            content_text = response.text[:2000]  # Only check first 2KB for validation
            
            # Compute SHA256 hash
            sha256_hash = hashlib.sha256(content_bytes).hexdigest()
            
            # Bonus: check for YAML frontmatter (common in skill files)
            has_frontmatter = content_text.strip().startswith('---')
            
            logger.info(f"Skill URL validated: {url} (content-type: {content_type}, frontmatter: {has_frontmatter}, sha256: {sha256_hash[:16]}...)")
            return True, "OK", sha256_hash
            
    except httpx.TimeoutException:
        reason = "Request timed out"
        logger.warning(f"Skill URL validation failed: {url} - {reason}")
        return False, reason, None
    except httpx.RequestError as e:
        reason = f"Request failed: {str(e)}"
        logger.warning(f"Skill URL validation failed: {url} - {reason}")
        return False, reason, None
    except Exception as e:
        reason = f"Unexpected error: {str(e)}"
        logger.error(f"Skill URL validation error: {url} - {reason}")
        return False, reason, None


# Solution operations
async def create_solution(
    db: AsyncSession,
    solution: SolutionCreate,
    api_key_hash: str,
) -> Solution:
    """Create a new solution."""
    # Validate the skill URL and compute SHA256
    is_valid, reason, sha256_hash = await validate_skill_url(solution.skill_url)
    if not is_valid:
        raise SkillURLValidationError(f"Skill URL validation failed: {reason}")
    
    db_solution = Solution(
        task_description=solution.task_description,
        skill_url=solution.skill_url,
        skill_sha256=sha256_hash,  # Server-computed, not user input
        tools_required=solution.tools_required,
        tags=solution.tags,
        submitter_key_hash=api_key_hash,
    )
    db.add(db_solution)
    await db.commit()
    await db.refresh(db_solution)
    return db_solution


async def get_solution(db: AsyncSession, solution_id: str) -> Optional[Solution]:
    """Get a solution by ID."""
    result = await db.execute(
        select(Solution).where(Solution.id == solution_id)
    )
    return result.scalar_one_or_none()


async def search_solutions(
    db: AsyncSession,
    task: Optional[str] = None,
    tags: Optional[list[str]] = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[Solution], int]:
    """Search solutions by task description and/or tags."""
    query = select(Solution).where(Solution.status == SolutionStatus.ACTIVE)
    count_query = select(func.count(Solution.id)).where(Solution.status == SolutionStatus.ACTIVE)
    
    if task:
        # Simple text search using LIKE
        search_term = f"%{task}%"
        query = query.where(Solution.task_description.ilike(search_term))
        count_query = count_query.where(Solution.task_description.ilike(search_term))
    
    # Note: JSON array filtering in SQLite is limited
    # For MVP, we'll filter in Python for tags
    
    # Order by success count descending
    query = query.order_by(Solution.success_count.desc(), Solution.last_updated.desc())
    
    # Get total count
    total_result = await db.execute(count_query)
    total = total_result.scalar()
    
    # Apply pagination
    query = query.offset(offset).limit(limit)
    
    result = await db.execute(query)
    solutions = list(result.scalars().all())
    
    # Filter by tags in Python (SQLite JSON limitation)
    if tags:
        solutions = [
            s for s in solutions
            if any(tag.lower() in [t.lower() for t in s.tags] for tag in tags)
        ]
        total = len(solutions)
    
    return solutions, total


async def get_recent_solutions(
    db: AsyncSession,
    limit: int = 10,
) -> list[Solution]:
    """Get recent successful solutions."""
    query = (
        select(Solution)
        .where(Solution.status == SolutionStatus.ACTIVE)
        .order_by(Solution.success_count.desc(), Solution.last_updated.desc())
        .limit(limit)
    )
    result = await db.execute(query)
    return list(result.scalars().all())


# Vote operations
async def create_or_update_vote(
    db: AsyncSession,
    solution_id: str,
    vote: VoteCreate,
    api_key_hash: str,
) -> tuple[Vote, bool]:
    """Create or update a vote. Returns (vote, is_new)."""
    # Check if vote exists
    result = await db.execute(
        select(Vote).where(
            Vote.solution_id == solution_id,
            Vote.api_key_hash == api_key_hash,
        )
    )
    existing_vote = result.scalar_one_or_none()
    
    solution = await get_solution(db, solution_id)
    if not solution:
        raise ValueError("Solution not found")
    
    if existing_vote:
        # Update existing vote
        old_type = existing_vote.vote_type
        if old_type != vote.vote:
            # Update counters
            if old_type == VoteType.SUCCESS:
                solution.success_count -= 1
            else:
                solution.failure_count -= 1
            
            if vote.vote == VoteType.SUCCESS:
                solution.success_count += 1
            else:
                solution.failure_count += 1
            
            existing_vote.vote_type = vote.vote
            existing_vote.voted_at = datetime.utcnow()
        
        existing_vote.agent_context = vote.context
        await db.commit()
        await db.refresh(existing_vote)
        return existing_vote, False
    else:
        # Create new vote
        db_vote = Vote(
            solution_id=solution_id,
            api_key_hash=api_key_hash,
            vote_type=vote.vote,
            agent_context=vote.context,
        )
        db.add(db_vote)
        
        # Update solution counters
        if vote.vote == VoteType.SUCCESS:
            solution.success_count += 1
        else:
            solution.failure_count += 1
        
        await db.commit()
        await db.refresh(db_vote)
        return db_vote, True


# Stats operations
async def get_stats(db: AsyncSession) -> dict:
    """Get overall statistics."""
    # Total solutions
    total_solutions = await db.execute(select(func.count(Solution.id)))
    total_solutions = total_solutions.scalar()
    
    # Active solutions
    active_solutions = await db.execute(
        select(func.count(Solution.id)).where(Solution.status == SolutionStatus.ACTIVE)
    )
    active_solutions = active_solutions.scalar()
    
    # Total votes
    total_votes = await db.execute(select(func.count(Vote.id)))
    total_votes = total_votes.scalar()
    
    # Success votes
    success_votes = await db.execute(
        select(func.count(Vote.id)).where(Vote.vote_type == VoteType.SUCCESS)
    )
    success_votes = success_votes.scalar()
    
    # Failure votes
    failure_votes = await db.execute(
        select(func.count(Vote.id)).where(Vote.vote_type == VoteType.FAILURE)
    )
    failure_votes = failure_votes.scalar()
    
    # Registered agents (active API keys)
    total_agents = await db.execute(
        select(func.count(APIKey.id)).where(APIKey.is_active == True)
    )
    total_agents = total_agents.scalar()
    
    # Top tags - get all solutions and count tags
    all_solutions = await db.execute(select(Solution.tags))
    all_tags = []
    for row in all_solutions.scalars():
        if row:
            all_tags.extend(row)
    
    tag_counts = Counter(all_tags)
    top_tags = [{"tag": tag, "count": count} for tag, count in tag_counts.most_common(10)]
    
    return {
        "total_solutions": total_solutions,
        "total_votes": total_votes,
        "total_success_votes": success_votes,
        "total_failure_votes": failure_votes,
        "top_tags": top_tags,
        "active_solutions": active_solutions,
        "total_agents": total_agents,
    }


# API Key operations
async def create_api_key(
    db: AsyncSession,
    key_hash: str,
    label: Optional[str] = None,
) -> APIKey:
    """Create a new API key record."""
    api_key = APIKey(
        key_hash=key_hash,
        label=label,
    )
    db.add(api_key)
    await db.commit()
    await db.refresh(api_key)
    return api_key


async def get_all_tags(db: AsyncSession) -> list[str]:
    """Get all unique tags."""
    all_solutions = await db.execute(
        select(Solution.tags).where(Solution.status == SolutionStatus.ACTIVE)
    )
    all_tags = set()
    for row in all_solutions.scalars():
        if row:
            all_tags.update(row)
    return sorted(all_tags)
