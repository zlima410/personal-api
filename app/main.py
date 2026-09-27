from fastapi import FastAPI

from app.routers import ingest

app = FastAPI(title="Personal API")

app.include_router(ingest.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
