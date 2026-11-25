import os
import sys
import tempfile
from pathlib import Path

import pytest

# Use a throwaway SQLite file for tests so they run without Postgres.
# Must be set before app.db is imported.
_tmp = Path(tempfile.gettempdir()) / "foodics_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}"
os.environ.setdefault("JWT_SECRET", "test-secret-that-is-long-enough-for-hs256-x")
os.environ.setdefault("GROQ_MODEL", "fake-model")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import Base, SessionLocal, engine  # noqa: E402
from app import models  # noqa: E402,F401


@pytest.fixture
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    yield session
    session.close()
