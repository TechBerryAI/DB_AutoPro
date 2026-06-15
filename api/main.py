"""FastAPI application entry point."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routes import auth, backup, fdw, migration, monitoring, patch, restore
from backend.core.config import get_settings
from backend.core.logging import setup_logging
from backend.services.scheduler_service import SchedulerService


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    scheduler = SchedulerService()
    scheduler.start()
    yield
    scheduler.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title=settings.app_name,
        description="Production-ready database administration automation platform",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(backup.router, prefix="/api/v1")
    app.include_router(restore.router, prefix="/api/v1")
    app.include_router(patch.router, prefix="/api/v1")
    app.include_router(fdw.router, prefix="/api/v1")
    app.include_router(migration.router, prefix="/api/v1")
    app.include_router(monitoring.router, prefix="/api/v1")

    @app.get("/", tags=["Root"])
    async def root():
        return {
            "app": settings.app_name,
            "version": "1.0.0",
            "docs": "/docs",
            "environment": settings.environment,
        }

    @app.get("/health", tags=["Health"])
    async def health():
        return {"status": "ok"}

    return app


app = create_app()
