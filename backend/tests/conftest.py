import os
import sys
import tempfile
from pathlib import Path

import pytest

# Use a throwaway SQLite file for tests so they run without Postgres.
# Must be set before app.db is imported.
_tmp = Path(tempfile.gettempdir()) / "foodics_test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}"
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
