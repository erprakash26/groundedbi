from fastapi import FastAPI

app = FastAPI(title="GroundedBI")


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness check: returns 200 if the process is up and serving requests."""
    return {"status": "ok"}
