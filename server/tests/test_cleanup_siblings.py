import datetime
import json

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.common.database import Base, connect_args
from app.modules.torrents.repository import TorrentRepository

KEEP_SEED_SECONDS = 24 * 60 * 60
NOW = datetime.datetime.now()
EXPIRED = NOW - datetime.timedelta(days=3)
RECENT = NOW - datetime.timedelta(hours=1)


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/app.db", connect_args=connect_args)
    Base.metadata.create_all(engine)
    yield sessionmaker(autocommit=False, autoflush=False, bind=engine)
    engine.dispose()


def _as_sqlite_text(value: datetime.datetime) -> str:
    return value.isoformat(sep=" ")


def add_torrent(
    db,
    indexer_id: str,
    torrent_id: str,
    alternates: list[dict[str, str]] | None = None,
) -> None:
    db.execute(text("PRAGMA foreign_keys=OFF"))
    db.execute(
        text(
            "INSERT INTO torrents (info_hash, indexer_id, torrent_id, is_persisted,"
            " alternates, updated_at, created_at) VALUES (:info_hash, :indexer_id,"
            " :torrent_id, 0, :alternates, :created_at, :created_at)"
        ),
        {
            "info_hash": f"hash-{indexer_id}",
            "indexer_id": indexer_id,
            "torrent_id": torrent_id,
            "alternates": json.dumps(alternates) if alternates is not None else None,
            "created_at": _as_sqlite_text(EXPIRED),
        },
    )


def add_playback(
    db, indexer_id: str, torrent_id: str, created_at: datetime.datetime
) -> None:
    db.execute(
        text(
            "INSERT INTO playback_histories (playback_id, user_id, indexer_id,"
            " torrent_id, file_index, torrent_name, file_name, created_at)"
            " VALUES (:playback_id, 'user', :indexer_id, :torrent_id, 0, 'name',"
            " 'file.mkv', :created_at)"
        ),
        {
            "playback_id": f"{indexer_id}-{torrent_id}-{created_at.timestamp()}",
            "indexer_id": indexer_id,
            "torrent_id": torrent_id,
            "created_at": _as_sqlite_text(created_at),
        },
    )


def cleanup_ids(session_factory, indexer_id: str) -> list[str]:
    with session_factory() as db:
        torrents = TorrentRepository(db).find_for_cleanup(
            indexer_id=indexer_id,
            keep_seed_seconds=KEEP_SEED_SECONDS,
        )
        return [torrent.torrent_id for torrent in torrents]


def seed_pair(session_factory, played_at: datetime.datetime | None) -> None:
    """Az ncore az elsődleges forrás, a bithumen a testvére."""
    with session_factory() as db:
        add_torrent(db, "ncore", "1", [{"indexer_id": "bithumen", "torrent_id": "2"}])
        add_torrent(db, "bithumen", "2")
        if played_at is not None:
            add_playback(db, "ncore", "1", played_at)
        db.commit()


def test_sibling_is_kept_while_the_primary_was_played_recently(session_factory):
    """Előzmény csak az elsődlegeshez készül, a testvér mégsem járhat le előbb."""
    seed_pair(session_factory, played_at=RECENT)

    assert cleanup_ids(session_factory, "ncore") == []
    assert cleanup_ids(session_factory, "bithumen") == []


def test_both_expire_together(session_factory):
    seed_pair(session_factory, played_at=EXPIRED)

    assert cleanup_ids(session_factory, "ncore") == ["1"]
    assert cleanup_ids(session_factory, "bithumen") == ["2"]


def test_primary_is_kept_while_the_sibling_was_played_recently(session_factory):
    with session_factory() as db:
        add_torrent(db, "ncore", "1", [{"indexer_id": "bithumen", "torrent_id": "2"}])
        add_torrent(db, "bithumen", "2")
        add_playback(db, "bithumen", "2", RECENT)
        db.commit()

    assert cleanup_ids(session_factory, "ncore") == []


def test_unrelated_torrent_still_expires(session_factory):
    seed_pair(session_factory, played_at=RECENT)
    with session_factory() as db:
        add_torrent(db, "majomparade", "3")
        db.commit()

    assert cleanup_ids(session_factory, "majomparade") == ["3"]
