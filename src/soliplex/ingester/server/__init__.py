import json
import logging

from fastapi import APIRouter
from fastapi import FastAPI
from fastapi import Form

from soliplex.ingester.lib import operations
from soliplex.ingester.lib.config import get_settings

from .routes.batch import batch_router
from .routes.document import doc_router
from .routes.stats import stats_router
from .routes.workflow import wf_router

logger = logging.getLogger(__name__)


async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    logger.info("Starting soliplex-ingester")
    import soliplex.ingester.lib.wf.runner as runner

    await runner.start_worker()
    yield
    logger.info("soliplex-ingester stopped")


app = FastAPI(lifespan=lifespan)
v1_router = APIRouter(prefix="/api/v1")


@v1_router.post("/source-status")
async def source_status(source: str = Form(...), hashes: str = Form(...)):
    hashes = json.loads(hashes)
    if not isinstance(hashes, dict):
        msg = "hashes must be a dictionary"
        raise TypeError(msg)
    status, to_delete = await operations.get_doc_status(source, hashes)
    return status


app.include_router(v1_router)
app.include_router(batch_router)
app.include_router(doc_router)
app.include_router(wf_router)
app.include_router(stats_router)
