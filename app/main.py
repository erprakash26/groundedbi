from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api import analytics, ask, web
from app.api.errors import INTERNAL_ERROR_RESPONSE, register_error_handlers
from app.observability.logging import configure_logging
from app.observability.middleware import RequestContextMiddleware


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    configure_logging()  # once at startup; safe if called again
    yield


def create_app() -> FastAPI:
    """Build the app. A factory so tests can build their own instance (e.g. with a test-only route)."""
    app = FastAPI(title="GroundedBI", lifespan=lifespan)
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
