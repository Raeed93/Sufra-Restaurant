from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL


class Base(DeclarativeBase):
    pass


engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db() -> None:
    """Create tables if they don't exist. Fine for a take-home; use Alembic in production."""
    from app import models  # noqa: F401  (registers the tables on Base)

    Base.metadata.create_all(engine)
