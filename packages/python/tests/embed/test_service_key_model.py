"""SQLModel for service_api_keys round-trips through the DB.

Pure model test — no FastAPI, no real DB connection beyond an in-memory
SQLite engine.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from awaithumans.server.db.models import ServiceAPIKey


def test_service_key_round_trip() -> None:
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    row = ServiceAPIKey(
        id="01HXSAMPLE",
        name="acme-prod",
        key_hash="a" * 64,
        key_prefix="ah_sk_abcdef",
        created_at=datetime.now(timezone.utc),
    )
    with Session(engine) as session:
        session.add(row)
        session.commit()

    with Session(engine) as session:
        loaded = session.exec(select(ServiceAPIKey).where(ServiceAPIKey.id == "01HXSAMPLE")).one()
        assert loaded.name == "acme-prod"
        assert loaded.key_hash == "a" * 64
        assert loaded.key_prefix == "ah_sk_abcdef"
        assert loaded.last_used_at is None
        assert loaded.revoked_at is None


def test_service_key_unique_hash_constraint() -> None:
    """Two rows with the same key_hash must conflict."""
    engine = create_engine("sqlite:///:memory:")
    SQLModel.metadata.create_all(engine)

    first = ServiceAPIKey(
        id="01A",
        name="alpha",
        key_hash="x" * 64,
        key_prefix="ah_sk_aaaaaa",
        created_at=datetime.now(timezone.utc),
    )
    second = ServiceAPIKey(
        id="01B",
        name="beta",
        key_hash="x" * 64,
        key_prefix="ah_sk_bbbbbb",
        created_at=datetime.now(timezone.utc),
    )
    with Session(engine) as session:
        session.add(first)
        session.commit()

    with Session(engine) as session:
        session.add(second)
        with pytest.raises(IntegrityError):
            session.commit()
