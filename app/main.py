from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import analytics, ask
from app.api.errors import register_error_handlers
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

    @app.get("/health")
    def health() -> dict[str, str]:
        """Liveness check: returns 200 if the process is up and serving requests."""
        return {"status": "ok"}

    return app


app = create_app()
