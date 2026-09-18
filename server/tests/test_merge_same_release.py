from unittest.mock import Mock

from app.modules.indexer_accounts.models import IndexerAccountModel
from app.modules.stream.schemas import StreamToken
from app.modules.stream.utils.stream_token import (
    generate_stream_token,
    parse_stream_token,
)
from app.modules.torrent_streams.schemas import TorrentStream
from app.modules.torrent_streams.service import TorrentStreamsService


def create_stream(indexer_id: str, torrent_id: str, name: str, seeders: int | None):
    token = generate_stream_token(
        StreamToken(
            indexer_id=indexer_id,
            torrent_id=torrent_id,
            file_index=0,
            playback_id="playback",
        )
    )
    return TorrentStream(
        indexer_account=IndexerAccountModel(
            indexer_id=indexer_id, username="test", password="pwd"
        ),
        torrent_id=torrent_id,
        info_hash=f"hash-{indexer_id}",
        torrent_name=name,
        file_name="movie.mkv",
        file_size=100,
        file_index=0,
        play_url=f"http://app/api/key/stream/{token}",
        seeders=seeders,
        is_persisted_torrent=False,
    )


def test_merge_same_release():
    service = TorrentStreamsService(
        db=Mock(),
        torrent_source_provider_service=Mock(),
        torrents_service=Mock(),
        settings_service=Mock(),
        preferences_service=Mock(),
    )

    merged = service._merge_same_release(
        [
            create_stream("ncore", "1", "Movie.2024.1080p", 10),
            create_stream("bithumen", "2", "movie.2024.1080p", None),
            create_stream("ncore", "3", "Other.2024.1080p", 5),
        ]
    )

    assert [stream.torrent_id for stream in merged] == ["1", "3"]
    assert merged[0].seeders == 10

    token = parse_stream_token(merged[0].play_url.rsplit("/", 1)[1])
    assert token.playback_id == "playback"
    assert [(a.indexer_id, a.torrent_id) for a in token.alternates] == [
        ("bithumen", "2")
    ]

    untouched = parse_stream_token(merged[1].play_url.rsplit("/", 1)[1])
    assert untouched.alternates == []
