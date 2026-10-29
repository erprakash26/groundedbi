import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import analytics, ask, web
from app.api.deps import SEED_HINT
from app.api.errors import INTERNAL_ERROR_RESPONSE, register_error_handlers
from app.api.http_policy import BodySizeLimitMiddleware, CacheControlMiddleware
from app.config import get_settings
from app.observability.logging import configure_logging
from app.observability.middleware import RequestContextMiddleware

logger = logging.getLogger("app.startup")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()  # once at startup; safe if called again
    # Same settings the requests use (tests can override them). A missing database is only
    # reported here: the app still starts, serves the pages, and get_db answers 503. Nothing
    # is created: the file is only ever opened read-only.
    settings = app.dependency_overrides.get(get_settings, get_settings)()
    if not settings.database_path.is_file():
        logger.error(
            "database_unavailable",
            extra={"event": "database_unavailable", "database_path": str(settings.database_path), "hint": SEED_HINT},
        )
    yield


def create_app() -> FastAPI:
    """Build the app. A factory so tests can build their own instance (e.g. with a test-only route)."""
    app = FastAPI(title="GroundedBI", lifespan=lifespan)
    # The last one added runs first: RequestContext wraps the others, so a 413 or a cached
    # response still gets a request ID, nosniff and an access log line.
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(CacheControlMiddleware)
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)
    app.include_router(analytics.router)
    app.include_router(ask.router)
    app.include_router(web.router)
    # A mount is never part of the OpenAPI schema.
    app.mount("/static", StaticFiles(directory=web.WEB_DIR), name="static")

    # No input and no database: only a 500 is possible besides 200.
    @app.get("/health", responses=INTERNAL_ERROR_RESPONSE)
    def health() -> dict[str, str]:
        """Liveness check: returns 200 if the process is up and serving requests."""
        return {"status": "ok"}

    return app


app = create_app()
