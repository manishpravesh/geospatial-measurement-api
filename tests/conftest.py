import warnings
from pathlib import Path

warnings.filterwarnings(
    "ignore",
    message=r"Using `httpx` with `starlette\.testclient` is deprecated",
)

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def build_app(directory: Path, **overrides):
    directory.mkdir(parents=True, exist_ok=True)
    settings = Settings(
        _env_file=None,
        database_url="sqlite:///" + (directory / "test.db").as_posix(),
        upload_dir=str(directory / "uploads"),
        **overrides,
    )
    return create_app(settings)


@pytest.fixture
def client(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as test_client:
        yield test_client
