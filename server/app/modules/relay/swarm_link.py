"""Azonos tartalmú, de eltérő info_hash-ű torrentek összekötése (kísérleti).

A privát trackerek saját `source` mezőt, darabméretet és fájlsorrendet
használnak, ezért ugyanaz a release külön swarm. A fájlok viszont ugyanoda
kerülnek a lemezen, így az egyik torrent kész darabjai bájt-tartományon át
leképezhetők a másik darabjaira.
"""

from collections.abc import Callable
from dataclasses import dataclass

from app.common.torrent_info import TorrentFileInfo, TorrentInfo


@dataclass(frozen=True)
class Segment:
    path: str
    offset: int
    length: int


def _common_files(
    a: TorrentInfo, b: TorrentInfo
) -> dict[str, tuple[TorrentFileInfo, TorrentFileInfo]]:
    b_files = {file.path: file for file in b.files}
    return {
        file.path: (file, b_files[file.path])
        for file in a.files
        if file.path in b_files and b_files[file.path].size == file.size
    }


def is_linkable(a: TorrentInfo, b: TorrentInfo) -> bool:
    """Ugyanaz a release: azonos név és legalább egy közös videó fájl."""
    if a.info_hash == b.info_hash or a.name != b.name:
        return False

    return any(file.is_video for file, _ in _common_files(a, b).values())


def piece_range(info: TorrentInfo, piece: int) -> tuple[int, int]:
    start = piece * info.piece_size
    return start, min(start + info.piece_size, info.size)


def piece_segments(info: TorrentInfo, piece: int) -> list[Segment]:
    """A darab bájtjai fájlonkénti szakaszokra bontva, sorrendben."""
    start, end = piece_range(info, piece)
    segments: list[Segment] = []

    for file in info.files:
        seg_start = max(start, file.offset)
        seg_end = min(end, file.offset + file.size)
        if seg_start < seg_end:
            segments.append(
                Segment(
                    path=file.path,
                    offset=seg_start - file.offset,
                    length=seg_end - seg_start,
                )
            )

    return segments


def pieces_for_segment(info: TorrentInfo, segment: Segment) -> range:
    file = next((file for file in info.files if file.path == segment.path), None)
    if file is None or segment.length <= 0:
        return range(0)

    start = file.offset + segment.offset
    end = start + segment.length
    return range(start // info.piece_size, (end - 1) // info.piece_size + 1)


def candidate_pieces(source: TorrentInfo, target: TorrentInfo, piece: int) -> set[int]:
    """A cél torrent azon darabjai, amelyeket a forrás darabja érint."""
    common = _common_files(source, target)
    candidates: set[int] = set()

    for segment in piece_segments(source, piece):
        if segment.path in common:
            candidates.update(pieces_for_segment(target, segment))

    return candidates


def bridgeable_segments(
    source: TorrentInfo,
    target: TorrentInfo,
    target_piece: int,
    source_has_piece: Callable[[int], bool],
) -> list[Segment] | None:
    """A cél darab lemezről olvasható szakaszai, ha a forrás már mindet letöltötte."""
    common = _common_files(source, target)
    segments = piece_segments(target, target_piece)

    covered = sum(segment.length for segment in segments)
    start, end = piece_range(target, target_piece)
    if not segments or covered != end - start:
        return None

    for segment in segments:
        if segment.path not in common:
            return None

        if not all(
            source_has_piece(piece) for piece in pieces_for_segment(source, segment)
        ):
            return None

    return segments
