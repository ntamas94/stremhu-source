from app.modules.relay.stream_buffer import (
    DEFAULT_PIECE_INTERVAL_MS,
    MAX_PREFETCH_PIECES,
    MIN_PIECE_INTERVAL_MS,
    buffered_piece_count,
    piece_interval_ms,
    prefetch_piece_count,
)

MIB = 1024 * 1024


def test_no_measurement_keeps_base_count():
    assert prefetch_piece_count(8, MIB, speed=0, buffer_seconds=20) == 8


def test_disabled_buffer_keeps_base_count():
    assert prefetch_piece_count(8, MIB, speed=10 * MIB, buffer_seconds=0) == 8


def test_count_covers_requested_seconds():
    # 5 MiB/s * 20 s = 100 MiB, 2 MiB-os darabokkal 50 darab
    assert prefetch_piece_count(4, 2 * MIB, speed=5 * MIB, buffer_seconds=20) == 50


def test_same_seconds_regardless_of_piece_size():
    small = prefetch_piece_count(8, MIB // 2, speed=4 * MIB, buffer_seconds=10)
    large = prefetch_piece_count(1, 16 * MIB, speed=4 * MIB, buffer_seconds=10)

    assert small * (MIB // 2) == 40 * MIB
    assert large * 16 * MIB >= 40 * MIB
    assert (large - 1) * 16 * MIB < 40 * MIB


def test_slow_stream_never_below_base_count():
    assert prefetch_piece_count(4, 16 * MIB, speed=100_000, buffer_seconds=10) == 4


def test_count_is_capped():
    count = prefetch_piece_count(8, 256 * 1024, speed=50 * MIB, buffer_seconds=120)
    assert count == MAX_PREFETCH_PIECES


def test_interval_matches_consumption():
    # 4 MiB/s mellett egy 2 MiB-os darab fél másodperc
    assert piece_interval_ms(2 * MIB, speed=4 * MIB) == 500


def test_interval_bounds():
    assert piece_interval_ms(MIB, speed=0) == DEFAULT_PIECE_INTERVAL_MS
    assert piece_interval_ms(16 * MIB, speed=MIB) == DEFAULT_PIECE_INTERVAL_MS
    assert piece_interval_ms(16 * 1024, speed=50 * MIB) == MIN_PIECE_INTERVAL_MS


def test_buffered_counts_contiguous_pieces_only():
    have = {10, 11, 12, 14}
    assert buffered_piece_count(have.__contains__, 10, 20) == 3


def test_buffered_zero_when_current_piece_missing():
    assert buffered_piece_count({11}.__contains__, 10, 20) == 0


def test_buffered_stops_at_stream_end_and_cap():
    assert buffered_piece_count(lambda _: True, 10, 12) == 3
    assert buffered_piece_count(lambda _: True, 0, 10_000) == MAX_PREFETCH_PIECES
