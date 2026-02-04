"""SQLAlchemy models for ClawSkills."""

import uuid
from datetime import datetime
from sqlalchemy import (
    Column, String, Integer, DateTime, Boolean, Text, ForeignKey,
    UniqueConstraint, JSON, Enum as SQLEnum
)
from sqlalchemy.orm import relationship, declarative_base
import enum

Base = declarative_base()


class SolutionStatus(str, enum.Enum):
    ACTIVE = "active"
    DEAD_LINK = "dead_link"
    DEPRECATED = "deprecated"


class VoteType(str, enum.Enum):
    SUCCESS = "success"
    FAILURE = "failure"


def generate_uuid() -> str:
    return str(uuid.uuid4())


class Solution(Base):
    """A skill solution for a task."""
    __tablename__ = "solutions"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    task_description = Column(Text, nullable=False, index=True)
    skill_url = Column(String(2048), nullable=False)
    skill_sha256 = Column(String(64), nullable=True)
    tools_required = Column(JSON, default=list)
    tags = Column(JSON, default=list)
    success_count = Column(Integer, default=0)
    failure_count = Column(Integer, default=0)
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_updated = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_verified = Column(DateTime, nullable=True)
    submitter_key_hash = Column(String(64), nullable=False)
    status = Column(SQLEnum(SolutionStatus), default=SolutionStatus.ACTIVE)

    votes = relationship("Vote", back_populates="solution", cascade="all, delete-orphan")


class Vote(Base):
    """A vote on a solution from an agent."""
    __tablename__ = "votes"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    solution_id = Column(String(36), ForeignKey("solutions.id"), nullable=False)
    api_key_hash = Column(String(64), nullable=False)
    vote_type = Column(SQLEnum(VoteType), nullable=False)
    voted_at = Column(DateTime, default=datetime.utcnow)
    agent_context = Column(Text, nullable=True)

    solution = relationship("Solution", back_populates="votes")

    __table_args__ = (
        UniqueConstraint("solution_id", "api_key_hash", name="unique_vote_per_key"),
    )


class APIKey(Base):
    """API key for authenticated access."""
    __tablename__ = "api_keys"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    key_hash = Column(String(64), unique=True, nullable=False, index=True)
    label = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)


class PendingRegistration(Base):
    """Pending API key registration awaiting email verification."""
    __tablename__ = "pending_registrations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    email = Column(String(255), nullable=False, index=True)
    verification_token = Column(String(64), unique=True, nullable=False, index=True)
    api_key_plain = Column(String(64), nullable=False)  # Store plain key until verified
    api_key_hash = Column(String(64), nullable=False)
    label = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    verified = Column(Boolean, default=False)
