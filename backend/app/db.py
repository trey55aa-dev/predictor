from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings


def _normalize_database_url(url: str) -> str:
    """SQLAlchemy needs an explicit driver in the URL scheme to use psycopg3
    -- a bare 'postgresql://' (what Neon and most hosts hand you) defaults to
    psycopg2, which isn't installed here. Rewriting it means the connection
    string from a host's dashboard can be pasted in as-is."""
    if url.startswith("postgresql://") and not url.startswith("postgresql+"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


database_url = _normalize_database_url(settings.database_url)
connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
engine = create_engine(database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    from app import models  # noqa: F401  (ensure models are registered)
    from app.models import Base

    Base.metadata.create_all(bind=engine)
