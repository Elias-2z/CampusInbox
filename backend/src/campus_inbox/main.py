"""Local API entry point. Business features are implemented in later milestones."""

from fastapi import FastAPI

app = FastAPI(title="CampusInbox API", version="0.1.0")


@app.get("/api/health", tags=["system"])
def health() -> dict[str, str]:
    """Check API availability; does not imply connectors or models are healthy."""
    return {"status": "ok", "service": "campus-inbox"}
