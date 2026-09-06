from app.db import _normalize_database_url


def test_normalize_bare_postgresql_url_adds_psycopg_driver():
    result = _normalize_database_url("postgresql://user:pass@host/dbname")
    assert result == "postgresql+psycopg://user:pass@host/dbname"


def test_normalize_leaves_explicit_driver_alone():
    result = _normalize_database_url("postgresql+psycopg://user:pass@host/dbname")
    assert result == "postgresql+psycopg://user:pass@host/dbname"


def test_normalize_leaves_sqlite_alone():
    result = _normalize_database_url("sqlite:///./football.db")
    assert result == "sqlite:///./football.db"
