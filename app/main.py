"""ClawSkills - AI Agent Skill Registry."""

import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Depends, HTTPException, Form, Query
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from sqlalchemy.ext.asyncio import AsyncSession

from . import __version__
from .database import init_db, get_db
from .models import APIKey
from .schemas import (
    SolutionCreate, SolutionResponse, SolutionListResponse,
    VoteCreate, VoteResponse, StatsResponse, HealthResponse,
)
from .auth import get_api_key, hash_key
from . import crud

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


def get_real_ip(request: Request) -> str:
    """Get real client IP, respecting X-Forwarded-For from reverse proxy."""
    # X-Forwarded-For can be comma-separated list: client, proxy1, proxy2
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        # First IP is the original client
        return forwarded.split(",")[0].strip()
    # X-Real-IP is simpler alternative
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    # Fallback to direct connection IP
    return request.client.host if request.client else "unknown"


# Rate limiting with real IP detection
limiter = Limiter(key_func=get_real_ip)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan - initialize DB on startup."""
    logger.info("Initializing database...")
    await init_db()
    
    # Bootstrap API key if provided
    seed_key = os.getenv("API_KEYS_SEED")
    if seed_key:
        from .database import async_session_maker
        async with async_session_maker() as db:
            key_hash = hash_key(seed_key)
            from sqlalchemy import select
            result = await db.execute(
                select(APIKey).where(APIKey.key_hash == key_hash)
            )
            if not result.scalar_one_or_none():
                await crud.create_api_key(db, key_hash, "Bootstrap Key")
                logger.info("Bootstrap API key created")
    
    logger.info("ClawSkills ready!")
    yield
    logger.info("Shutting down...")


app = FastAPI(
    title="ClawSkills",
    description="AI Agent Skill Registry - Share and discover skills that help agents solve tasks",
    version=__version__,
    lifespan=lifespan,
)

# Add rate limiting
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files and templates
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "templates"))


# ============ API Endpoints ============

@app.get("/health", response_model=HealthResponse, tags=["Health"])
@limiter.limit("100/minute")
async def health_check(request: Request):
    """Health check endpoint."""
    return HealthResponse(status="ok", version=__version__)


@app.get("/api/stats", response_model=StatsResponse, tags=["Stats"])
@limiter.limit("100/minute")
async def get_stats(request: Request, db: AsyncSession = Depends(get_db)):
    """Get overall statistics."""
    return await crud.get_stats(db)


@app.get("/api/solutions", response_model=SolutionListResponse, tags=["Solutions"])
@limiter.limit("100/minute")
async def list_solutions(
    request: Request,
    task: str = Query(None, description="Search query for task description"),
    tags: str = Query(None, description="Comma-separated tags to filter by"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Search and list solutions."""
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    solutions, total = await crud.search_solutions(db, task, tag_list, limit, offset)
    return SolutionListResponse(
        solutions=[SolutionResponse.model_validate(s) for s in solutions],
        total=total,
        limit=limit,
        offset=offset,
    )


@app.get("/api/solutions/{solution_id}", response_model=SolutionResponse, tags=["Solutions"])
@limiter.limit("100/minute")
async def get_solution(
    request: Request,
    solution_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Get a specific solution by ID."""
    solution = await crud.get_solution(db, solution_id)
    if not solution:
        raise HTTPException(status_code=404, detail="Solution not found")
    return SolutionResponse.model_validate(solution)


@app.post("/api/solutions", response_model=SolutionResponse, status_code=201, tags=["Solutions"])
@limiter.limit("10/minute")
async def create_solution(
    request: Request,
    solution: SolutionCreate,
    db: AsyncSession = Depends(get_db),
    api_key: APIKey = Depends(get_api_key),
):
    """Create a new solution (requires API key)."""
    db_solution = await crud.create_solution(db, solution, api_key.key_hash)
    logger.info(f"Solution created: {db_solution.id} by key {api_key.label or api_key.id}")
    return SolutionResponse.model_validate(db_solution)


@app.post("/api/solutions/{solution_id}/vote", response_model=VoteResponse, tags=["Votes"])
@limiter.limit("10/minute")
async def vote_solution(
    request: Request,
    solution_id: str,
    vote: VoteCreate,
    db: AsyncSession = Depends(get_db),
    api_key: APIKey = Depends(get_api_key),
):
    """Vote on a solution (requires API key)."""
    try:
        db_vote, is_new = await crud.create_or_update_vote(
            db, solution_id, vote, api_key.key_hash
        )
        action = "created" if is_new else "updated"
        logger.info(f"Vote {action}: {db_vote.id} on solution {solution_id}")
        return VoteResponse.model_validate(db_vote)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ============ Web UI Routes ============

@app.get("/", response_class=HTMLResponse, tags=["Web UI"])
@limiter.limit("100/minute")
async def web_index(request: Request, db: AsyncSession = Depends(get_db)):
    """Homepage with search and recent solutions."""
    recent = await crud.get_recent_solutions(db, limit=10)
    all_tags = await crud.get_all_tags(db)
    stats = await crud.get_stats(db)
    return templates.TemplateResponse("index.html", {
        "request": request,
        "recent_solutions": recent,
        "all_tags": all_tags,
        "stats": stats,
    })


@app.get("/search", response_class=HTMLResponse, tags=["Web UI"])
@limiter.limit("100/minute")
async def web_search(
    request: Request,
    task: str = Query(None),
    tags: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Search results page."""
    tag_list = [t.strip() for t in tags.split(",")] if tags else None
    solutions, total = await crud.search_solutions(db, task, tag_list, limit=50, offset=0)
    all_tags = await crud.get_all_tags(db)
    return templates.TemplateResponse("search.html", {
        "request": request,
        "solutions": solutions,
        "total": total,
        "query": task or "",
        "selected_tags": tag_list or [],
        "all_tags": all_tags,
    })


@app.get("/solution/{solution_id}", response_class=HTMLResponse, tags=["Web UI"])
@limiter.limit("100/minute")
async def web_solution(
    request: Request,
    solution_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Solution detail page."""
    solution = await crud.get_solution(db, solution_id)
    if not solution:
        raise HTTPException(status_code=404, detail="Solution not found")
    return templates.TemplateResponse("solution.html", {
        "request": request,
        "solution": solution,
    })


@app.get("/submit", response_class=HTMLResponse, tags=["Web UI"])
@limiter.limit("100/minute")
async def web_submit_form(request: Request, db: AsyncSession = Depends(get_db)):
    """Submit form page."""
    all_tags = await crud.get_all_tags(db)
    return templates.TemplateResponse("submit.html", {
        "request": request,
        "all_tags": all_tags,
        "error": None,
        "success": None,
    })


@app.post("/submit", response_class=HTMLResponse, tags=["Web UI"])
@limiter.limit("10/minute")
async def web_submit(
    request: Request,
    task_description: str = Form(...),
    skill_url: str = Form(...),
    skill_sha256: str = Form(None),
    tools_required: str = Form(""),
    tags: str = Form(""),
    api_key: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Handle submit form."""
    all_tags = await crud.get_all_tags(db)
    
    # Validate API key
    from sqlalchemy import select
    key_hash = hash_key(api_key)
    result = await db.execute(
        select(APIKey).where(APIKey.key_hash == key_hash, APIKey.is_active == True)
    )
    db_key = result.scalar_one_or_none()
    
    if not db_key:
        return templates.TemplateResponse("submit.html", {
            "request": request,
            "all_tags": all_tags,
            "error": "Invalid API key",
            "success": None,
        })
    
    # Create solution
    try:
        tools_list = [t.strip() for t in tools_required.split(",") if t.strip()]
        tags_list = [t.strip() for t in tags.split(",") if t.strip()]
        
        solution_data = SolutionCreate(
            task_description=task_description,
            skill_url=skill_url,
            skill_sha256=skill_sha256 or None,
            tools_required=tools_list,
            tags=tags_list,
        )
        
        db_solution = await crud.create_solution(db, solution_data, key_hash)
        return RedirectResponse(
            url=f"/solution/{db_solution.id}",
            status_code=303,
        )
    except Exception as e:
        logger.error(f"Error creating solution: {e}")
        return templates.TemplateResponse("submit.html", {
            "request": request,
            "all_tags": all_tags,
            "error": str(e),
            "success": None,
        })


@app.post("/solution/{solution_id}/vote", response_class=HTMLResponse, tags=["Web UI"])
@limiter.limit("10/minute")
async def web_vote(
    request: Request,
    solution_id: str,
    vote: str = Form(...),
    api_key: str = Form(...),
    context: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Handle vote form."""
    # Validate API key
    from sqlalchemy import select
    key_hash = hash_key(api_key)
    result = await db.execute(
        select(APIKey).where(APIKey.key_hash == key_hash, APIKey.is_active == True)
    )
    db_key = result.scalar_one_or_none()
    
    if not db_key:
        solution = await crud.get_solution(db, solution_id)
        return templates.TemplateResponse("solution.html", {
            "request": request,
            "solution": solution,
            "error": "Invalid API key",
        })
    
    try:
        from .models import VoteType
        vote_type = VoteType.SUCCESS if vote == "success" else VoteType.FAILURE
        vote_data = VoteCreate(vote=vote_type, context=context or None)
        await crud.create_or_update_vote(db, solution_id, vote_data, key_hash)
        return RedirectResponse(url=f"/solution/{solution_id}", status_code=303)
    except Exception as e:
        solution = await crud.get_solution(db, solution_id)
        return templates.TemplateResponse("solution.html", {
            "request": request,
            "solution": solution,
            "error": str(e),
        })


@app.get("/stats", response_class=HTMLResponse, tags=["Web UI"])
@limiter.limit("100/minute")
async def web_stats(request: Request, db: AsyncSession = Depends(get_db)):
    """Stats page."""
    stats = await crud.get_stats(db)
    return templates.TemplateResponse("stats.html", {
        "request": request,
        "stats": stats,
    })


@app.get("/about", response_class=HTMLResponse, tags=["Web UI"])
@limiter.limit("100/minute")
async def web_about(request: Request):
    """About page."""
    return templates.TemplateResponse("about.html", {
        "request": request,
    })
