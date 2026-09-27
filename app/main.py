from fastapi import FastAPI

from app.routers import admin, ingest, v1

app = FastAPI(
    title="Personal API",
    openapi_tags=[
        {"name": "v1", "description": "Read the data. Requires READ_API_KEY."},
        {"name": "public", "description": "Unauthenticated summary."},
        {"name": "ingest", "description": "Push data in. Requires INGEST_API_KEY."},
        {"name": "admin", "description": "Trigger syncs. Requires ADMIN_API_KEY."},
    ],
)

app.include_router(v1.router)
app.include_router(v1.public_router)
app.include_router(ingest.router)
app.include_router(admin.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
