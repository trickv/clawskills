"""Tests for ClawdSkills API."""

import pytest
from httpx import AsyncClient, ASGITransport
import os

# Set test database before importing app
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

from app.main import app
from app.database import init_db, async_session_maker
from app.auth import generate_api_key
from app.crud import create_api_key


@pytest.fixture(autouse=True)
async def setup_db():
    """Initialize database for each test."""
    await init_db()
    yield


@pytest.fixture
async def api_key() -> str:
    """Create and return a test API key."""
    raw_key, key_hash = generate_api_key()
    async with async_session_maker() as db:
        await create_api_key(db, key_hash, "Test Key")
    return raw_key


@pytest.fixture
async def client():
    """Create test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


@pytest.mark.asyncio
async def test_health(client):
    """Test health endpoint."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert "version" in data


@pytest.mark.asyncio
async def test_stats(client):
    """Test stats endpoint."""
    response = await client.get("/api/stats")
    assert response.status_code == 200
    data = response.json()
    assert "total_solutions" in data
    assert "total_votes" in data


@pytest.mark.asyncio
async def test_create_solution_requires_auth(client):
    """Test that creating a solution requires auth."""
    response = await client.post("/api/solutions", json={
        "task_description": "Test task description here",
        "skill_url": "https://github.com/example/skill",
        "tools_required": ["bash"],
        "tags": ["test"],
    })
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_create_solution(client, api_key):
    """Test creating a solution with valid API key."""
    response = await client.post(
        "/api/solutions",
        json={
            "task_description": "Test task description for creating solutions",
            "skill_url": "https://github.com/example/skill",
            "tools_required": ["bash", "file_write"],
            "tags": ["test", "automation"],
        },
        headers={"X-API-Key": api_key},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["task_description"] == "Test task description for creating solutions"
    assert data["skill_url"] == "https://github.com/example/skill"
    assert data["success_count"] == 0
    assert data["failure_count"] == 0
    return data["id"]


@pytest.mark.asyncio
async def test_get_solution(client, api_key):
    """Test getting a solution by ID."""
    # Create a solution first
    create_response = await client.post(
        "/api/solutions",
        json={
            "task_description": "Another test task description",
            "skill_url": "https://github.com/example/skill2",
            "tools_required": [],
            "tags": [],
        },
        headers={"X-API-Key": api_key},
    )
    solution_id = create_response.json()["id"]
    
    # Get the solution
    response = await client.get(f"/api/solutions/{solution_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == solution_id


@pytest.mark.asyncio
async def test_search_solutions(client, api_key):
    """Test searching solutions."""
    # Create a solution
    await client.post(
        "/api/solutions",
        json={
            "task_description": "Send email via Gmail API",
            "skill_url": "https://github.com/example/gmail-skill",
            "tools_required": ["http_request"],
            "tags": ["gmail", "email"],
        },
        headers={"X-API-Key": api_key},
    )
    
    # Search for it
    response = await client.get("/api/solutions?task=gmail")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert any("gmail" in s["task_description"].lower() for s in data["solutions"])


@pytest.mark.asyncio
async def test_vote_on_solution(client, api_key):
    """Test voting on a solution."""
    # Create a solution
    create_response = await client.post(
        "/api/solutions",
        json={
            "task_description": "Parse JSON files efficiently",
            "skill_url": "https://github.com/example/json-skill",
            "tools_required": ["file_read"],
            "tags": ["json", "parsing"],
        },
        headers={"X-API-Key": api_key},
    )
    solution_id = create_response.json()["id"]
    
    # Vote success
    vote_response = await client.post(
        f"/api/solutions/{solution_id}/vote",
        json={"vote": "success", "context": "Worked great!"},
        headers={"X-API-Key": api_key},
    )
    assert vote_response.status_code == 200
    
    # Check solution was updated
    get_response = await client.get(f"/api/solutions/{solution_id}")
    data = get_response.json()
    assert data["success_count"] == 1
    assert data["failure_count"] == 0


@pytest.mark.asyncio
async def test_vote_update(client, api_key):
    """Test updating a vote."""
    # Create solution
    create_response = await client.post(
        "/api/solutions",
        json={
            "task_description": "Deploy to Kubernetes cluster",
            "skill_url": "https://github.com/example/k8s-skill",
            "tools_required": ["kubectl"],
            "tags": ["kubernetes", "deployment"],
        },
        headers={"X-API-Key": api_key},
    )
    solution_id = create_response.json()["id"]
    
    # Vote success
    await client.post(
        f"/api/solutions/{solution_id}/vote",
        json={"vote": "success"},
        headers={"X-API-Key": api_key},
    )
    
    # Change vote to failure
    await client.post(
        f"/api/solutions/{solution_id}/vote",
        json={"vote": "failure", "context": "Actually it broke"},
        headers={"X-API-Key": api_key},
    )
    
    # Check counts were updated correctly
    get_response = await client.get(f"/api/solutions/{solution_id}")
    data = get_response.json()
    assert data["success_count"] == 0
    assert data["failure_count"] == 1


@pytest.mark.asyncio
async def test_web_ui_pages(client):
    """Test that web UI pages load."""
    pages = ["/", "/search", "/submit", "/stats"]
    for page in pages:
        response = await client.get(page)
        assert response.status_code == 200, f"Page {page} failed"


@pytest.mark.asyncio
async def test_solution_not_found(client):
    """Test 404 for non-existent solution."""
    response = await client.get("/api/solutions/nonexistent-id")
    assert response.status_code == 404
