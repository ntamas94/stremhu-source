import math
from collections.abc import Callable

# Ennél több darabra nem adunk határidőt egy streamnek, különben a libtorrent
# time-critical listája túl nagyra nő (kis darabméret + gyors kliens esetén).
MAX_PREFETCH_PIECES = 512

DEFAULT_PIECE_INTERVAL_MS = 1000
MIN_PIECE_INTERVAL_MS = 50


def prefetch_piece_count(
    base_count: int,
    piece_size: int,
    speed: int,
    buffer_seconds: int,
) -> int:
    """Hány darabot töltsünk előre, hogy `buffer_seconds` másodpercnyi adat legyen a kliens előtt.

    A `speed` a kliensnek ténylegesen kiküldött adat sebessége (bájt / másodperc).
    Amíg nincs mérés (frissen indult stream), a `base_count` az alsó határ.
    """
    if speed <= 0 or buffer_seconds <= 0 or piece_size <= 0:
        return base_count

    wanted = math.ceil((speed * buffer_seconds) / piece_size)
    return max(base_count, min(wanted, MAX_PREFETCH_PIECES))


def piece_interval_ms(piece_size: int, speed: int) -> int:
    """Ennyi idő alatt fogyaszt el a kliens egy darabot; a határidők lépésköze.

    Sosem lazább a korábbi fix 1 másodpercnél.
    """
    if speed <= 0 or piece_size <= 0:
        return DEFAULT_PIECE_INTERVAL_MS

    interval = int((piece_size / speed) * 1000)
    return max(MIN_PIECE_INTERVAL_MS, min(interval, DEFAULT_PIECE_INTERVAL_MS))


def buffered_piece_count(
    has_piece: Callable[[int], bool],
    start_piece: int,
    end_piece: int,
) -> int:
    """Hány darab van meg egybefüggően a lejátszási pozíciótól előre."""
    last_piece = min(end_piece, start_piece + MAX_PREFETCH_PIECES - 1)
    count = 0

    for piece_index in range(start_piece, last_piece + 1):
        if not has_piece(piece_index):
            break
        count += 1

    return count
