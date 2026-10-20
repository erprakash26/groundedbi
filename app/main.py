from fastapi import FastAPI

from app.api import analytics

app = FastAPI(title="GroundedBI")
app.include_router(analytics.router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check: returns 200 if the process is up and serving requests."""
    return {"status": "ok"}
