import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter
from fastapi import Depends
from fastapi import FastAPI
from fastapi import Form
from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from soliplex.ingester.lib import operations
from soliplex.ingester.lib.auth import get_current_user
from soliplex.ingester.lib.config import get_settings
from soliplex.ingester.lib.models import Database

from .routes.batch import batch_router
from .routes.document import doc_router
from .routes.lancedb import lancedb_router
from .routes.stats import stats_router
from .routes.sync import router as sync_router
from .routes.workflow import wf_router

logger = logging.getLogger(__name__)

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level,
        format=settings.log_format,
        datefmt="%Y-%m-%dT%H:%M:%S",
        style="{",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logger.info("Starting soliplex-ingester")

    # Initialize database before starting worker
    await Database.initialize()

    import soliplex.ingester.lib.wf.runner as runner

    await runner.start_worker()
    yield

    # Cleanup
    await Database.close()
    logger.info("soliplex-ingester stopped")


app = FastAPI(lifespan=lifespan)

# Add rate limiter to app state
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Get settings for middleware configuration
settings = get_settings()

# Configure CORS with environment-based origins
allowed_origins = settings.allowed_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,  # Restrict to specific domains
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],  # Be explicit
    allow_headers=["Content-Type", "Authorization", "X-Forwarded-User", "X-Forwarded-Email"],  # Be specific
)

# Add trusted host middleware (configure for production)
trusted_hosts = settings.trusted_hosts.split(",")
app.add_middleware(TrustedHostMiddleware, allowed_hosts=trusted_hosts)


# Add security headers middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add security headers to all responses."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    # Only add HSTS in production with HTTPS
    if settings.enable_hsts:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# API router with authentication dependency
# Auth is only enforced when API_KEY_ENABLED=true or AUTH_TRUST_PROXY_HEADERS=true
v1_router = APIRouter(prefix="/api/v1", dependencies=[Depends(get_current_user)])


@v1_router.post("/source-status")
async def source_status(
    source: str = Form(...),
    hashes: str = Form(...),
    delete_stale: bool = Form(False),
):
    hashes = json.loads(hashes)
    if not isinstance(hashes, dict):
        msg = "hashes must be a dictionary"
        raise TypeError(msg)
    if delete_stale:
        status, deleted_count = await operations.update_doc_status(source, hashes)
        return {"status": status, "deleted_count": deleted_count}
    status, to_delete = await operations.get_doc_status(source, hashes)
    return status


app.include_router(v1_router)
app.include_router(batch_router)
app.include_router(doc_router)
app.include_router(lancedb_router)
app.include_router(wf_router)
app.include_router(stats_router)
app.include_router(sync_router)

# Serve UI static assets
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    # Mount static assets at /_app
    app.mount("/_app", StaticFiles(directory=static_dir / "_app"), name="static-assets")

    # Catch-all route for SPA - serves index.html for all non-API routes
    @app.get("/{full_path:path}")
    async def serve_spa(request: Request, full_path: str):
        """Serve the SPA for all routes not caught by API or static assets."""
        # Serve static files from root if they exist (like favicon.ico)
        file_path = static_dir / full_path
        if file_path.is_file():
            return FileResponse(file_path)
        # Otherwise serve index.html for SPA routing
        return FileResponse(static_dir / "index.html")
