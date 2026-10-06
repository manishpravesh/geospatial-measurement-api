import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import __version__
from app.api.files import router as files_router
from app.config import Settings
from app.database import init_database

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.getLogger("app").setLevel(logging.INFO)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine, factory = init_database(app.state.settings)
        app.state.engine = engine
        app.state.session_factory = factory
        yield
        engine.dispose()

    app = FastAPI(
        title="Geospatial File Measurement API",
        version=__version__,
        summary="Upload a shapefile or KML file and measure its features.",
        description=(
            "Accepts a zipped shapefile, a KML file, or a KMZ archive. "
            "Area and length are calculated in metres after projecting "
            "geographic coordinates into a local CRS. Latitude and longitude "
            "are never treated as distance units."
        ),
        lifespan=lifespan,
        redirect_slashes=False,
        openapi_tags=[
            {
                "name": "files",
                "description": "Upload geospatial files and read their measurements.",
            },
            {"name": "health", "description": "Service health."},
        ],
    )
    app.state.settings = settings
    app.include_router(files_router)

    @app.get("/health", tags=["health"])
    def health(request: Request):
        session = request.app.state.session_factory()
        try:
            session.execute(text("SELECT 1"))
        except Exception:
            logger.exception("health check failed")
            return JSONResponse(status_code=503, content={"status": "unavailable"})
        finally:
            session.close()
        return {"status": "ok"}

    @app.get("/", include_in_schema=False)
    def root():
        return {
            "service": "geospatial-file-measurement",
            "docs": "/docs",
            "health": "/health",
        }

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
