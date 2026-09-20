from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio

if TYPE_CHECKING:
    from app.modules.relay.service import RelayService

import content_types
import libtorrent as libtorrent
from fastapi import Request

from app.common.constants import (
    CHUNK_SIZE,
    PRIO_0,
)
from app.common.logger import logger
from app.common.torrent_info import TorrentFileInfo, TorrentInfo
from app.modules.relay.multi_torrent import candidate_pieces
from app.modules.relay.speed_meter import SpeedMeter


class Torrent:
    def __init__(
        self,
        torrent_handle: libtorrent.torrent_handle,
        torrent_info: TorrentInfo,
        service: RelayService,
        default_priority: int,
    ):
        self.torrent_handle = torrent_handle
        self.service = service

        self.info = torrent_info
        self.info_hash = torrent_info.info_hash
        self.name = torrent_info.name
        self.total_size = torrent_info.size

        self.piece_size = torrent_info.piece_size

        self.critical_piece_count = max(
            1,
            min(
                math.ceil((1024 * 1024) / self.piece_size),
                2,
            ),
        )

        self.prefetch_piece_count = self.critical_piece_count * 4

        self._default_piece_priority = default_priority

        self._current_piece_priority = default_priority

        self._max_connections = self.service._torrent_connections_limit

        self._active_deadlines: dict[int, int] = {}

        # Kísérleti multi torrent: azonos tartalmú, más info_hash-ű torrentek.
        self.siblings: list[Torrent] = []
        self.bridged_pieces: set[int] = set()
        self._mirror_sources: set[str] = set()
        self._mirrored_deadlines: dict[str, dict[int, int]] = {}

        self.files: dict[int, File] = {}

        for file_info in torrent_info.files:
            self.files[file_info.index] = File(
                file_info=file_info,
                torrent=self,
            )

    @property
    def has_active_streams(self) -> bool:
        return any(file.has_active_streams for file in self.files.values())

    @property
    def is_hot(self) -> bool:
        """Saját stream vagy egy testvér streamje miatt aktívan tölt."""
        return self.has_active_streams or bool(self._mirror_sources)

    def _update_prioritize_pieces(self, priority: int) -> None:
        priorities = self.torrent_handle.piece_priorities()
        self.torrent_handle.prioritize_pieces([priority] * len(priorities))
        self._current_piece_priority = priority

    def priority_manager(self):
        try:
            self._apply_heat()

            target_deadlines = self.get_deadlines()

            for piece_index in list(self._active_deadlines.keys()):
                if piece_index not in target_deadlines:
                    self.torrent_handle.reset_piece_deadline(piece_index)
                    self._active_deadlines.pop(piece_index)

            for piece_index, deadline in target_deadlines.items():
                self.torrent_handle.set_piece_deadline(
                    piece_index,
                    deadline,
                    libtorrent.torrent_handle.alert_when_available,
                )
                self._active_deadlines[piece_index] = deadline

            self._mirror_deadlines(target_deadlines)

            if (
                not self.is_hot
                and self._current_piece_priority != self._default_piece_priority
            ):
                self._update_prioritize_pieces(self._default_piece_priority)

        except Exception:
            logger.exception("Hiba történt a prioritáskezelőben.")

    def _apply_heat(self) -> None:
        # A PRIO_0 törli a deadline-okat, ezért mindig a deadline-ok előtt fut.
        if self.is_hot and self._current_piece_priority != PRIO_0:
            self._update_prioritize_pieces(PRIO_0)

        target_max_connections = (
            50 if self.is_hot else self.service._torrent_connections_limit
        )

        if target_max_connections != self._max_connections:
            self.torrent_handle.set_max_connections(target_max_connections)
            self._max_connections = target_max_connections

    def _mirror_deadlines(self, target_deadlines: dict[int, int]) -> None:
        """A lejátszási ablakot a testvér torrentek darabjaira is ráteszi."""
        for sibling in list(self.siblings):
            sibling_targets = self._sibling_deadlines(sibling, target_deadlines)
            mirrored = self._mirrored_deadlines.setdefault(sibling.info_hash, {})

            if sibling_targets:
                sibling.add_mirror_source(self.info_hash)

            for sibling_piece in list(mirrored):
                if sibling_piece not in sibling_targets:
                    sibling.torrent_handle.reset_piece_deadline(sibling_piece)
                    mirrored.pop(sibling_piece)

            for sibling_piece, deadline in sibling_targets.items():
                sibling.torrent_handle.set_piece_deadline(sibling_piece, deadline)
                mirrored[sibling_piece] = deadline

            if not sibling_targets:
                sibling.remove_mirror_source(self.info_hash)

    def _sibling_deadlines(
        self, sibling: Torrent, target_deadlines: dict[int, int]
    ) -> dict[int, int]:
        """A testvér hiányzó darabjai, a rájuk eső legkorábbi deadline-nal."""
        if sibling.has_active_streams:
            return {}

        sibling_targets: dict[int, int] = {}
        for piece_index, deadline in target_deadlines.items():
            for sibling_piece in candidate_pieces(self.info, sibling.info, piece_index):
                if sibling.torrent_handle.have_piece(sibling_piece):
                    continue
                sibling_targets[sibling_piece] = min(
                    deadline, sibling_targets.get(sibling_piece, deadline)
                )

        return sibling_targets

    def add_mirror_source(self, info_hash: str) -> None:
        """Egy testvér lejátszása miatt ez a torrent is aktívan tölt."""
        if info_hash in self._mirror_sources:
            return

        self._mirror_sources.add(info_hash)
        self._apply_heat()

    def remove_mirror_source(self, info_hash: str) -> None:
        if info_hash not in self._mirror_sources:
            return

        self._mirror_sources.discard(info_hash)
        self.service.trigger_priority_update(self.info_hash)

    def link(self, other: Torrent) -> None:
        if other not in self.siblings:
            self.siblings.append(other)
        if self not in other.siblings:
            other.siblings.append(self)

    def unlink(self) -> None:
        for sibling in self.siblings:
            if self in sibling.siblings:
                sibling.siblings.remove(self)
            sibling._mirrored_deadlines.pop(self.info_hash, None)
            sibling.remove_mirror_source(self.info_hash)
        self.siblings = []

    def update_default_priority(
        self,
        priority: int,
    ) -> None:
        self._default_piece_priority = priority
        self.service.trigger_priority_update(self.info_hash)

    def get_deadlines(self) -> dict[int, int]:

        target_deadlines: dict[int, int] = {}

        for file in self.files.values():
            if not file.has_active_streams:
                continue

            self._set_file_boundary_priorities(file, target_deadlines)

            for stream in list(file.streams.values()):
                if stream.is_destroying:
                    continue

                pieces_to_fetch: list[tuple[int, int]] = []

                for piece_index in range(
                    stream.current_stream_piece, file.end_piece_index + 1
                ):
                    if not self.torrent_handle.have_piece(piece_index):
                        distance = piece_index - stream.current_stream_piece
                        pieces_to_fetch.append((piece_index, distance))
                        if len(pieces_to_fetch) >= self.prefetch_piece_count:
                            break

                if len(pieces_to_fetch) < self.prefetch_piece_count:
                    distance_to_end_of_file = (
                        file.end_piece_index - stream.current_stream_piece
                    ) + 1

                    for piece_index in range(
                        file.start_piece_index, stream.current_stream_piece
                    ):
                        if not self.torrent_handle.have_piece(piece_index):
                            piece_distance = distance_to_end_of_file + (
                                piece_index - file.start_piece_index
                            )
                            pieces_to_fetch.append((piece_index, piece_distance))
                            if len(pieces_to_fetch) >= self.prefetch_piece_count:
                                break

                for piece_index, piece_distance in pieces_to_fetch:
                    if piece_index == stream.current_stream_piece:
                        deadline = 0
                    else:
                        deadline = 2000 + (piece_distance * 1000)

                    if (
                        piece_index not in target_deadlines
                        or deadline < target_deadlines[piece_index]
                    ):
                        target_deadlines[piece_index] = deadline

        return target_deadlines

    def _set_file_boundary_priorities(
        self,
        file: File,
        target_deadlines: dict[int, int],
    ) -> None:
        for piece_index in {file.start_piece_index, file.end_piece_index}:
            if not self.torrent_handle.have_piece(piece_index):
                target_deadlines[piece_index] = 0


class File:
    def __init__(
        self,
        file_info: TorrentFileInfo,
        torrent: Torrent,
    ):
        self.torrent = torrent
        self.name = file_info.name
        self.size = file_info.size
        self.offset = file_info.offset

        self.start_piece_index = file_info.offset // torrent.piece_size
        self.end_piece_index = (file_info.offset + self.size - 1) // torrent.piece_size

        content_type = content_types.get_content_type(self.name)
        self.is_video = content_type.startswith("video/") if content_type else False

        self.streams: dict[str, Stream] = {}

    @property
    def has_active_streams(self) -> bool:
        return len(self.streams) > 0

    async def stream(
        self,
        playback_id: str,
        user_id: str,
        stream_start_byte: int,
        stream_end_byte: int,
        request: Request,
    ) -> AsyncIterator[bytes]:
        stream = Stream(
            stream_id=str(uuid4()),
            playback_id=playback_id,
            user_id=user_id,
            torrent=self.torrent,
            file=self,
            stream_start_byte=stream_start_byte,
            stream_end_byte=stream_end_byte,
        )

        return await stream.start(request)


class Stream:
    def __init__(
        self,
        stream_id: str,
        playback_id: str,
        user_id: str,
        torrent: Torrent,
        file: File,
        stream_start_byte: int,
        stream_end_byte: int,
    ):

        self.id = stream_id
        self.playback_id = playback_id
        self.user_id = user_id
        self.torrent = torrent
        self.file = file
        self.start_byte = stream_start_byte
        self.end_byte = stream_end_byte

        stream_start_piece_index, stream_end_piece_index = self._get_byte_to_piece(
            stream_start_byte=stream_start_byte,
            stream_end_byte=stream_end_byte,
        )

        self.stream_start_piece_index = stream_start_piece_index
        self.stream_end_piece_index = stream_end_piece_index
        self.current_stream_piece = stream_start_piece_index

        self.is_destroying = False
        self._speed_meter = SpeedMeter()

        self.file.streams[self.id] = self

    @property
    def stream_pieces_range(self) -> range:
        return range(self.current_stream_piece, self.stream_end_piece_index + 1)

    @property
    def speed(self) -> int:
        """A kliensnek ténylegesen kiküldött adat sebessége (bájt / másodperc)."""
        return self._speed_meter.speed

    @property
    def current_stream_byte(self) -> int:
        byte_offset = (
            self.current_stream_piece * self.torrent.piece_size
        ) - self.file.offset
        return max(0, min(byte_offset, self.file.size))

    async def destroy(self):
        self.is_destroying = True
        self.torrent.service.trigger_priority_update(self.torrent.info_hash)

        await asyncio.sleep(0.25)

        if self.id in self.file.streams:
            del self.file.streams[self.id]

        self.torrent.service.trigger_priority_update(self.torrent.info_hash)

    def _get_byte_to_piece(
        self,
        stream_start_byte: int,
        stream_end_byte: int,
    ):
        stream_start_piece_index = (
            stream_start_byte + self.file.offset
        ) // self.torrent.piece_size

        stream_end_piece_index = (
            stream_end_byte + self.file.offset
        ) // self.torrent.piece_size

        return stream_start_piece_index, stream_end_piece_index

    def get_critical_pieces(self) -> list[int]:
        prefetch_end = min(
            self.stream_end_piece_index,
            self.current_stream_piece + self.torrent.prefetch_piece_count,
        )

        critical_pieces: list[int] = []
        for piece_index in range(self.current_stream_piece, prefetch_end + 1):
            if not self.torrent.torrent_handle.have_piece(piece_index):
                critical_pieces.append(piece_index)
                if len(critical_pieces) >= self.torrent.critical_piece_count:
                    break

        return critical_pieces

    async def start(
        self,
        request: Request,
    ) -> AsyncIterator[bytes]:
        return self._stream_inner(request)

    async def _stream_inner(
        self,
        request: Request,
    ) -> AsyncIterator[bytes]:
        try:
            for piece_index in range(
                self.stream_start_piece_index, self.stream_end_piece_index + 1
            ):
                self.current_stream_piece = piece_index

                self.torrent.service.trigger_priority_update(self.torrent.info_hash)

                if await request.is_disconnected():
                    break

                piece_buffer = await self.torrent.service.get_piece_data(
                    self.torrent.torrent_handle, piece_index
                )

                start_offset = 0
                end_offset = len(piece_buffer)

                if piece_index == self.stream_start_piece_index:
                    start_offset = (
                        self.start_byte + self.file.offset
                    ) % self.torrent.piece_size

                if piece_index == self.stream_end_piece_index:
                    end_offset = (
                        self.end_byte + self.file.offset
                    ) % self.torrent.piece_size + 1

                view = memoryview(piece_buffer)[start_offset:end_offset]

                disconnected = False
                for chunk_start in range(0, len(view), CHUNK_SIZE):
                    if await request.is_disconnected():
                        disconnected = True
                        break
                    chunk = view[chunk_start : chunk_start + CHUNK_SIZE].tobytes()
                    yield chunk
                    # A yield után fut, vagyis amikor a kliens már átvette.
                    self._speed_meter.add(len(chunk))

                if disconnected:
                    break

        except anyio.get_cancelled_exc_class():
            pass
        except Exception:
            logger.exception("Hiba történt a fájl streamelése közben.")
        finally:
            asyncio.create_task(self.destroy())
