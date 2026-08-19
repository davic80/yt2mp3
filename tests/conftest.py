import os
import tempfile

import pytest

# Point the app at throwaway storage *before* create_app() reads the config.
os.environ.setdefault("DATABASE_URL", "sqlite://")          # in-memory
os.environ.setdefault("DOWNLOAD_DIR", tempfile.mkdtemp())
os.environ.setdefault("SECRET_KEY", "test-secret")


@pytest.fixture
def app():
    from app import create_app
    application = create_app()
    application.config.update(TESTING=True)
    return application


@pytest.fixture
def client(app):
    return app.test_client()
