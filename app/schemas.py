"""Pydantic schemas for API validation."""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, ConfigDict
from .models import SolutionStatus, VoteType


# Solution schemas
class SolutionCreate(BaseModel):
    """Schema for creating a new solution."""
    task_description: str = Field(..., min_length=10, max_length=5000)
    skill_url: str = Field(..., max_length=2048)
    # Note: skill_sha256 is computed server-side, not accepted from user input
    tools_required: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class SolutionResponse(BaseModel):
    """Schema for solution response."""
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    task_description: str
    skill_url: str
    skill_sha256: Optional[str]
    tools_required: list[str]
    tags: list[str]
    success_count: int
    failure_count: int
    first_seen: datetime
    last_updated: datetime
    last_verified: Optional[datetime]
    status: SolutionStatus


class SolutionListResponse(BaseModel):
    """Schema for paginated solution list."""
    solutions: list[SolutionResponse]
    total: int
    limit: int
    offset: int


# Vote schemas
class VoteCreate(BaseModel):
    """Schema for creating a vote."""
    vote: VoteType
    context: Optional[str] = Field(None, max_length=1000)


class VoteResponse(BaseModel):
    """Schema for vote response."""
    model_config = ConfigDict(from_attributes=True)
    
    id: str
    solution_id: str
    vote_type: VoteType
    voted_at: datetime
    agent_context: Optional[str]


# Stats schemas
class StatsResponse(BaseModel):
    """Schema for stats response."""
    total_solutions: int
    total_votes: int
    total_success_votes: int
    total_failure_votes: int
    top_tags: list[dict]
    active_solutions: int
    total_agents: int


# Health check
class HealthResponse(BaseModel):
    """Schema for health check response."""
    status: str = "ok"
    version: str
