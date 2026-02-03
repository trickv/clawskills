"""Database CRUD operations."""

from datetime import datetime
from typing import Optional
from collections import Counter
from sqlalchemy import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from .models import Solution, Vote, APIKey, SolutionStatus, VoteType
from .schemas import SolutionCreate, VoteCreate
from .auth import hash_key


# Solution operations
async def create_solution(
    db: AsyncSession,
    solution: SolutionCreate,
    api_key_hash: str,
) -> Solution:
    """Create a new solution."""
    db_solution = Solution(
        task_description=solution.task_description,
        skill_url=solution.skill_url,
        skill_sha256=solution.skill_sha256,
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
