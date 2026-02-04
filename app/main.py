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
from .models import APIKey, PendingRegistration
from datetime import datetime, timedelta
from .schemas import (
    SolutionCreate, SolutionResponse, SolutionListResponse,
    VoteCreate, VoteResponse, StatsResponse, HealthResponse,
)
from .auth import get_api_key, hash_key
from . import crud
from .crud import SkillURLValidationError

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
    try:
        db_solution = await crud.create_solution(db, solution, api_key.key_hash)
        logger.info(f"Solution created: {db_solution.id} by key {api_key.label or api_key.id}")
        return SolutionResponse.model_validate(db_solution)
    except SkillURLValidationError as e:
        logger.warning(f"Solution rejected: {solution.skill_url} - {e}")
        raise HTTPException(status_code=400, detail=str(e))


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


@app.get("/skill.md", tags=["Skills"])
@limiter.limit("100/minute")
async def skill_md(request: Request):
    """Serve the SKILL.md file for AI agents."""
    from fastapi.responses import FileResponse
    skill_path = os.path.join(BASE_DIR, "SKILL.md")
    return FileResponse(skill_path, media_type="text/markdown")


# ============ Registration API (for agents) ============

@app.post("/api/register", tags=["Registration"])
@limiter.limit("5/minute")
async def api_register(
    request: Request,
    email: str = Query(..., description="Email address for verification"),
    label: str = Query(None, description="Optional label for this API key"),
    db: AsyncSession = Depends(get_db),
):
    """
    Register for an API key (for agents).
    
    1. Agent calls this with user's email
    2. User receives email with verification code
    3. User gives code to agent
    4. Agent calls /api/verify with code to activate key
    """
    import secrets
    from .email import send_verification_code_email
    from sqlalchemy import select
    
    email = email.strip().lower()
    
    # Basic email validation
    if not email or "@" not in email or "." not in email:
        raise HTTPException(status_code=400, detail="Invalid email address")
    
    # Check if email already has an active key
    result = await db.execute(
        select(APIKey).where(APIKey.email == email, APIKey.is_active == True)
    )
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=400, 
            detail="This email already has an active API key"
        )
    
    # Check for recent pending registration (rate limit)
    result = await db.execute(
        select(PendingRegistration).where(
            PendingRegistration.email == email,
            PendingRegistration.verified == False,
            PendingRegistration.expires_at > datetime.utcnow(),
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        # Return the existing pending registration info
        return {
            "status": "pending",
            "message": "Verification email already sent. Ask your human for the code.",
            "api_key": existing.api_key_plain,
            "email": email,
        }
    
    # Generate API key and short verification code
    api_key_plain = f"csk_{secrets.token_urlsafe(32)}"
    # Short code: VERIFY-XXXX (easy to read/type)
    verification_code = f"VERIFY-{secrets.token_hex(3).upper()}"
    key_hash = hash_key(api_key_plain)
    
    # Create pending registration
    pending = PendingRegistration(
        email=email,
        verification_token=verification_code,
        api_key_plain=api_key_plain,
        api_key_hash=key_hash,
        label=label or email.split("@")[0],
        expires_at=datetime.utcnow() + timedelta(hours=24),
    )
    db.add(pending)
    await db.commit()
    
    # Send verification email with code
    email_sent = await send_verification_code_email(email, verification_code)
    
    if not email_sent:
        logger.error(f"Failed to send registration email to {email}")
        raise HTTPException(status_code=500, detail="Failed to send verification email")
    
    logger.info(f"Registration email sent to {email}")
    
    return {
        "status": "pending",
        "message": "Verification email sent. Ask your human for the code from their email.",
        "api_key": api_key_plain,
        "email": email,
        "note": "Save this API key! It won't be shown again. The key won't work until verified."
    }


@app.post("/api/verify", tags=["Registration"])
@limiter.limit("10/minute")
async def api_verify(
    request: Request,
    code: str = Query(..., description="Verification code from email"),
    db: AsyncSession = Depends(get_db),
):
    """
    Verify email and activate API key (for agents).
    
    Agent calls this with the verification code the human received via email.
    """
    from sqlalchemy import select
    
    code = code.strip().upper()
    
    # Find pending registration
    result = await db.execute(
        select(PendingRegistration).where(
            PendingRegistration.verification_token == code,
            PendingRegistration.verified == False,
        )
    )
    pending = result.scalar_one_or_none()
    
    if not pending:
        raise HTTPException(status_code=400, detail="Invalid verification code")
    
    if pending.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Verification code expired. Please register again.")
    
    # Create the actual API key
    api_key = APIKey(
        key_hash=pending.api_key_hash,
        label=pending.label,
        email=pending.email,
    )
    db.add(api_key)
    
    # Mark as verified
    pending.verified = True
    
    await db.commit()
    
    logger.info(f"API key verified and activated for {pending.email}")
    
    return {
        "status": "verified",
        "message": "API key activated! You can now use ClawSkills.",
        "api_key": pending.api_key_plain,
    }


# ============ Registration Web UI (fallback for humans) ============

@app.get("/register", response_class=HTMLResponse, tags=["Registration"])
@limiter.limit("20/minute")
async def register_form(request: Request):
    """Registration form page (for humans without an agent)."""
    return templates.TemplateResponse("register.html", {
        "request": request,
        "error": None,
        "success": None,
    })


@app.post("/register", response_class=HTMLResponse, tags=["Registration"])
@limiter.limit("5/minute")
async def register_submit(
    request: Request,
    email: str = Form(...),
    label: str = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """Handle registration form - send verification email."""
    import secrets
    from .email import send_verification_code_email
    from sqlalchemy import select
    
    email = email.strip().lower()
    
    # Basic email validation
    if not email or "@" not in email or "." not in email:
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "Please enter a valid email address",
            "success": None,
        })
    
    # Check if email already has an active key
    result = await db.execute(
        select(APIKey).where(APIKey.email == email, APIKey.is_active == True)
    )
    if result.scalar_one_or_none():
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "This email already has an active API key. Contact support if you need help.",
            "success": None,
        })
    
    # Check for recent pending registration (rate limit)
    result = await db.execute(
        select(PendingRegistration).where(
            PendingRegistration.email == email,
            PendingRegistration.verified == False,
            PendingRegistration.expires_at > datetime.utcnow(),
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": None,
            "success": f"Verification already pending. Check your email for the code, then enter it below.",
            "show_verify": True,
            "api_key": existing.api_key_plain,
        })
    
    # Generate API key and short verification code
    api_key_plain = f"csk_{secrets.token_urlsafe(32)}"
    verification_code = f"VERIFY-{secrets.token_hex(3).upper()}"
    key_hash = hash_key(api_key_plain)
    
    # Create pending registration
    pending = PendingRegistration(
        email=email,
        verification_token=verification_code,
        api_key_plain=api_key_plain,
        api_key_hash=key_hash,
        label=label or email.split("@")[0],
        expires_at=datetime.utcnow() + timedelta(hours=24),
    )
    db.add(pending)
    await db.commit()
    
    # Send verification email with code
    email_sent = await send_verification_code_email(email, verification_code)
    
    if email_sent:
        logger.info(f"Registration email sent to {email}")
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": None,
            "success": f"Verification email sent to {email}. Enter the code below.",
            "show_verify": True,
            "api_key": api_key_plain,
        })
    else:
        logger.error(f"Failed to send registration email to {email}")
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error": "Failed to send email. Please try again later.",
            "success": None,
        })


@app.post("/verify", response_class=HTMLResponse, tags=["Registration"])
@limiter.limit("10/minute")
async def verify_web(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Verify via web form."""
    from sqlalchemy import select
    
    code = code.strip().upper()
    
    result = await db.execute(
        select(PendingRegistration).where(
            PendingRegistration.verification_token == code,
            PendingRegistration.verified == False,
        )
    )
    pending = result.scalar_one_or_none()
    
    if not pending:
        return templates.TemplateResponse("verify.html", {
            "request": request,
            "error": "Invalid verification code.",
            "api_key": None,
        })
    
    if pending.expires_at < datetime.utcnow():
        return templates.TemplateResponse("verify.html", {
            "request": request,
            "error": "Verification code expired. Please register again.",
            "api_key": None,
        })
    
    # Create the actual API key
    api_key = APIKey(
        key_hash=pending.api_key_hash,
        label=pending.label,
        email=pending.email,
    )
    db.add(api_key)
    pending.verified = True
    await db.commit()
    
    logger.info(f"API key verified and activated for {pending.email}")
    
    return templates.TemplateResponse("verify.html", {
        "request": request,
        "error": None,
        "api_key": pending.api_key_plain,
    })
