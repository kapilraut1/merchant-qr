from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import __version__
from app.api.invoices import router as invoices_router
from app.api.merchants import router as merchants_router
from app.api.settlements import router as settlements_router
from app.api.transactions import router as transactions_router
from app.api.wallets import router as wallets_router
from app.config import get_settings
from app.db import get_db

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield


app = FastAPI(
    title="Merchant QR Settlement API",
    description=(
        "Simulates how a digital wallet settles merchant QR payments and "
        "reconciles the resulting transactions."
    ),
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["ops"], summary="Liveness probe")
def health() -> dict[str, str]:
    """Process-level liveness. Intentionally touches no dependency so that a
    database blip cannot trigger a container restart loop."""
    return {
        "status": "ok",
        "service": settings.app_name,
        "version": __version__,
    }


@app.get("/health/ready", tags=["ops"], summary="Readiness probe")
def readiness(db: Session = Depends(get_db)) -> dict[str, object]:
    """Readiness probe. Verifies the API can actually reach PostgreSQL, which
    makes it an end-to-end check of the backend -> database wiring."""
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        return {
            "status": "degraded",
            "service": settings.app_name,
            "database": "unavailable",
            "detail": exc.__class__.__name__,
        }
    return {
        "status": "ok",
        "service": settings.app_name,
        "database": "ok",
    }


app.include_router(merchants_router)
app.include_router(wallets_router)
app.include_router(invoices_router)
app.include_router(transactions_router)
app.include_router(settlements_router)


@app.get("/", tags=["ops"], summary="Service banner")
def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "version": __version__,
        "docs": "/docs",
        "health": "/health",
    }
