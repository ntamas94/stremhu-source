from app.common.torrent_info import TorrentFileInfo, TorrentInfo
from app.modules.relay.multi_torrent import (
    Segment,
    bridgeable_segments,
    candidate_pieces,
    is_linkable,
    piece_segments,
)

MIB = 1024 * 1024
VIDEO = ("Movie/Movie.mkv", 19818140089)
SAMPLE = ("Movie/SAMPLE.mkv", 200207997)
NFO = ("Movie/Movie.nfo", 9228)


def create_info(
    info_hash: str, piece_size: int, files: list[tuple[str, int]], name: str = "Movie"
) -> TorrentInfo:
    offset = 0
    file_infos = []
    for index, (path, size) in enumerate(files):
        file_infos.append(
            TorrentFileInfo(
                name=path.split("/")[-1],
                path=path,
                index=index,
                size=size,
                offset=offset,
                start_piece_index=offset // piece_size,
                end_piece_index=(offset + size - 1) // piece_size,
                is_video=path.endswith(".mkv"),
            )
        )
        offset += size

    return TorrentInfo(
        info_hash=info_hash,
        name=name,
        size=offset,
        piece_size=piece_size,
        files=file_infos,
    )


# Valós eset: azonos fájlok, más sorrend és darabméret.
NCORE = create_info("a" * 40, 2 * MIB, [VIDEO, SAMPLE, NFO])
BITHUMEN = create_info("b" * 40, 16 * MIB, [SAMPLE, VIDEO, NFO])


def test_is_linkable():
    assert is_linkable(NCORE, BITHUMEN)
    assert not is_linkable(NCORE, NCORE)
    assert not is_linkable(NCORE, create_info("c" * 40, 2 * MIB, [VIDEO], "Other"))
    assert not is_linkable(
        NCORE, create_info("d" * 40, 2 * MIB, [("Movie/Movie.mkv", 5), NFO])
    )


def test_piece_segments_cover_whole_piece_across_files():
    last_piece = (NCORE.size - 1) // NCORE.piece_size
    for info in (NCORE, BITHUMEN):
        for piece in (0, 11, (info.size - 1) // info.piece_size):
            start = piece * info.piece_size
            expected = min(info.piece_size, info.size - start)
            assert sum(s.length for s in piece_segments(info, piece)) == expected

    assert [s.path for s in piece_segments(NCORE, last_piece)][-1] == NFO[0]


def test_bridge_large_piece_needs_every_small_piece():
    # A BitHUmen 20. darabja teljesen a videóba esik.
    target_piece = 20
    segments = piece_segments(BITHUMEN, target_piece)
    assert segments == [
        Segment(path=VIDEO[0], offset=20 * 16 * MIB - SAMPLE[1], length=16 * MIB)
    ]

    start = segments[0].offset
    needed = set(range(start // (2 * MIB), (start + 16 * MIB - 1) // (2 * MIB) + 1))
    # A darabhatárok nem esnek egybe, ezért 9 kis darab kell.
    assert len(needed) == 9

    assert (
        bridgeable_segments(NCORE, BITHUMEN, target_piece, needed.__contains__)
        == segments
    )

    missing_one = needed - {min(needed)}
    assert (
        bridgeable_segments(NCORE, BITHUMEN, target_piece, missing_one.__contains__)
        is None
    )

    for piece in needed:
        assert target_piece in candidate_pieces(NCORE, BITHUMEN, piece)


def test_bridge_small_pieces_from_one_large_piece():
    source_piece = 20
    candidates = candidate_pieces(BITHUMEN, NCORE, source_piece)
    assert len(candidates) == 9

    bridgeable = {
        piece
        for piece in candidates
        if bridgeable_segments(BITHUMEN, NCORE, piece, {source_piece}.__contains__)
    }
    # A két szélső kis darab átlóg a szomszéd nagy darabba.
    assert len(bridgeable) == 7
    assert bridgeable == candidates - {min(candidates), max(candidates)}


def test_piece_outside_common_files_is_not_bridgeable():
    other = create_info("e" * 40, 2 * MIB, [VIDEO, ("Movie/extra.bin", 4 * MIB)])
    assert is_linkable(NCORE, other)

    extra_piece = (other.size - 1) // other.piece_size
    assert bridgeable_segments(NCORE, other, extra_piece, lambda _: True) is None
