import asyncio
import time

from fastapi import HTTPException

from app.common.database import isolated_db_session
from app.common.keyed_lock import KeyedLock
from app.common.logger import logger
from app.common.schemas.internal import ImdbInfo
from app.modules.indexers.service import IndexersService
from app.modules.playback_histories.dependencies import (
    create_playback_histories_service,
)
from app.modules.playback_histories.schemas.internal import (
    PlaybackHistoryClientInfo,
    PlaybackHistoryCreate,
)
from app.modules.relay.entities import File
from app.modules.relay.service import RelayService
from app.modules.settings.service import SettingsService
from app.modules.stream.schemas import (
    ParsedRangeHeader,
    StreamAlternate,
    StreamToken,
)
from app.modules.torrent_files.isolated_service import IsolatedTorrentFilesService
from app.modules.torrent_files.models import TorrentFileModel
from app.modules.torrent_files.schemas import TorrentFileIdentifier
from app.modules.torrent_files.service import TorrentFilesService
from app.modules.torrents.dependencies import (
    create_torrents_service,
)
from app.modules.torrents.schemas.internal import TorrentUpdate, TorrentWithRelay
from app.modules.torrents.service import TorrentsService

torrent_locks = KeyedLock()
playback_history_lock = KeyedLock()

# Hibás forrást ennyi ideig nem próbálunk újra (minden range kérés ide fut be).
FAILED_SOURCE_TTL_SECONDS = 300
failed_sources: dict[str, float] = {}


class StreamService:
    def __init__(
        self,
        torrents_service: TorrentsService,
        torrent_files_service: TorrentFilesService,
        indexers_service: IndexersService,
        relay_service: RelayService,
        settings_service: SettingsService,
        isolated_torrent_files_service: IsolatedTorrentFilesService,
    ):
        self._torrents_service = torrents_service
        self._torrent_files_service = torrent_files_service
        self._indexers_service = indexers_service
        self._relay_service = relay_service
        self._settings_service = settings_service
        self._isolated_torrent_files_service = isolated_torrent_files_service

    async def prepare_for_stream(
        self,
        range_header: str | None,
        indexer_id: str,
        torrent_id: str,
        file_index: int,
        alternates: list[StreamAlternate] | None = None,
    ) -> tuple[ParsedRangeHeader, File]:
        sources = [
            StreamAlternate(
                indexer_id=indexer_id, torrent_id=torrent_id, file_index=file_index
            ),
            *(alternates or []),
        ]

        file: File | None = None
        last_error: Exception | None = None

        # ponytail: a tartalék csak indítási hibára vált (indexer/torrent fájl),
        # halott swarm-ot nem érzékel; ahhoz piece-timeout kellene a relay-ben.
        for index, source in enumerate(sources):
            source_key = f"{source.indexer_id}:{source.torrent_id}"
            is_last = index == len(sources) - 1
            if (
                not is_last
                and time.monotonic()
                - failed_sources.get(source_key, -FAILED_SOURCE_TTL_SECONDS)
                < FAILED_SOURCE_TTL_SECONDS
            ):
                continue

            others = [
                {"indexer_id": other.indexer_id, "torrent_id": other.torrent_id}
                for other in sources
                if other is not source
            ]

            try:
                torrent_with_relay, created = await self._ensure_torrent(source, others)
            except Exception as error:
                failed_sources[source_key] = time.monotonic()
                logger.warning(
                    f"Forrás nem indítható ({source.indexer_id}:{source.torrent_id}): {error}"
                )
                last_error = error
                continue

            file = self._relay_service.get_torrent_file(
                info_hash=torrent_with_relay.info_hash,
                file_index=source.file_index,
            )

            dual_swarm = await asyncio.to_thread(self._settings_service.is_dual_swarm)
            if created or dual_swarm:
                await self._attach_alternate_sources(
                    torrent_with_relay.info_hash, sources[index + 1 :], dual_swarm
                )
            break

        if file is None:
            assert last_error is not None
            raise last_error

        parsed_range_header = self._parse_range_header(
            file_size=file.size,
            range_header=range_header,
        )

        return parsed_range_header, file

    async def _ensure_torrent(
        self,
        source: StreamAlternate,
        alternates: list[dict[str, str]] | None = None,
    ) -> tuple[TorrentWithRelay, bool]:
        indexer_id = source.indexer_id
        torrent_id = source.torrent_id

        self._isolated_torrent_files_service.touch(
            TorrentFileIdentifier(indexer_id=indexer_id, torrent_id=torrent_id)
        )

        async with torrent_locks(f"{indexer_id}:{torrent_id}"):
            torrent_with_relay: TorrentWithRelay | None = await asyncio.to_thread(
                self._torrents_service.find_by_id,
                indexer_id=indexer_id,
                torrent_id=torrent_id,
            )

            if torrent_with_relay is not None:
                # Régebben indított torrentnél pótoljuk a tartalék forrásokat.
                if alternates and torrent_with_relay.torrent.alternates is None:
                    await asyncio.to_thread(
                        self._save_alternates,
                        torrent_with_relay.info_hash,
                        alternates,
                    )
                return torrent_with_relay, False

            torrent_file = await asyncio.to_thread(
                self._torrent_files_service.find_by_id,
                indexer_id=indexer_id,
                torrent_id=torrent_id,
            )

            if torrent_file is None:
                indexer_torrent = (
                    await self._indexers_service.get_torrent_by_torrent_id(
                        indexer_id=indexer_id, torrent_id=torrent_id
                    )
                )
                downloaded_torrent_file = await self._indexers_service.download_torrent(
                    indexer_id=indexer_id,
                    torrent_id=torrent_id,
                    download_url=indexer_torrent.download_url,
                )

                torrent_file = await asyncio.to_thread(
                    self._isolated_torrent_files_service.create,
                    indexer_id=indexer_id,
                    torrent_id=torrent_id,
                    torrent_bytes=downloaded_torrent_file.torrent_bytes,
                )

            torrent_with_relay = await asyncio.to_thread(
                self._create_torrent,
                torrent_file=torrent_file,
                file_index=source.file_index,
                alternates=alternates,
            )
            return torrent_with_relay, True

    async def _attach_alternate_sources(
        self, info_hash: str, sources: list[StreamAlternate], dual_swarm: bool
    ) -> None:
        """Azonos info_hash-ű alternatívák trackereit a futó torrenthez adja.

        Dupla swarm mellett az azonos tartalmú, de más info_hash-ű alternatíva
        külön torrentként indul, a relay köti össze őket.

        Csak a már cache-elt torrent fájlokat nézi, indexert nem hív, és a
        lejátszást sosem töri meg.
        """
        for source in sources:
            try:
                torrent_file = await asyncio.to_thread(
                    self._torrent_files_service.find_by_id,
                    indexer_id=source.indexer_id,
                    torrent_id=source.torrent_id,
                )
                if torrent_file is None:
                    continue

                same_hash = torrent_file.info.info_hash == info_hash
                linkable = dual_swarm and self._relay_service.is_linkable(
                    info_hash, torrent_file.info
                )
                if not same_hash and not linkable:
                    continue

                await self._ensure_torrent(source)
            except Exception as error:
                logger.warning(
                    f"Tracker egyesítés sikertelen ({source.indexer_id}:{source.torrent_id}): {error}"
                )

    async def save_playback_history(
        self,
        stream_token: StreamToken,
        client_info: PlaybackHistoryClientInfo,
        user_id: str,
        file: File,
        imdb_info: ImdbInfo | None = None,
    ) -> None:
        try:
            async with playback_history_lock(stream_token.playback_id):
                await asyncio.to_thread(
                    self._save_playback_history,
                    PlaybackHistoryCreate(
                        client=client_info,
                        indexer_id=stream_token.indexer_id,
                        playback_id=stream_token.playback_id,
                        user_id=user_id,
                        torrent_id=stream_token.torrent_id,
                        file_index=stream_token.file_index,
                        imdb_info=imdb_info,
                        torrent_name=file.torrent.name,
                        file_name=file.name,
                    ),
                )
        except Exception as e:
            logger.warning(f"Nem sikerült menteni a lejátszási előzményt: {e}")

    def _save_playback_history(self, payload: PlaybackHistoryCreate) -> None:
        """Külön session: egy sikertelen írás nem viheti magával a kérés tranzakcióját."""
        with isolated_db_session() as local_db:
            create_playback_histories_service(local_db).get_or_create(payload)

    def _create_torrent(
        self,
        torrent_file: TorrentFileModel,
        file_index: int,
        alternates: list[dict[str, str]] | None = None,
    ) -> TorrentWithRelay:
        self._validate_file(torrent_file, file_index)

        with isolated_db_session() as local_db:
            return create_torrents_service(local_db).create_from_torrent_file(
                torrent_file, alternates
            )

    def _save_alternates(
        self, info_hash: str, alternates: list[dict[str, str]]
    ) -> None:
        with isolated_db_session() as local_db:
            create_torrents_service(local_db).update(
                info_hash, TorrentUpdate(alternates=alternates)
            )

    def _validate_file(
        self,
        torrent_file: TorrentFileModel,
        file_index: int,
    ):
        if file_index not in range(len(torrent_file.info.files)):
            raise HTTPException(400, "Érvénytelen fájl index.")

        file_info = torrent_file.info.files[file_index]
        if not file_info.is_video:
            raise HTTPException(400, "A megadott fájl nem videó.")

    def _parse_range_header(
        self,
        file_size: int,
        range_header: str | None = None,
    ) -> ParsedRangeHeader:
        if range_header is None:
            return ParsedRangeHeader(
                start_byte=0,
                end_byte=file_size - 1,
                content_length=file_size,
            )

        if not range_header.startswith("bytes="):
            raise HTTPException(416, "Érvénytelen range header.")

        range_value = range_header.replace("bytes=", "", 1).strip()
        if "," in range_value:
            raise HTTPException(416, "A több tartomány nem támogatott.")

        start_byte_str, end_byte_str = range_value.split("-", 1)

        if start_byte_str == "":
            if not end_byte_str:
                raise HTTPException(416, "Érvénytelen range header.")

            suffix_length = int(end_byte_str)
            if suffix_length <= 0:
                raise HTTPException(416, "Érvénytelen range header.")

            start_byte = max(file_size - suffix_length, 0)
            end_byte = file_size - 1
        else:
            start_byte = int(start_byte_str)
            end_byte = int(end_byte_str) if end_byte_str else file_size - 1

        if (
            start_byte < 0
            or end_byte < 0
            or start_byte > end_byte
            or end_byte >= file_size
        ):
            raise HTTPException(416, "A kért tartomány kívül esik a fájlon.")

        content_length = end_byte - start_byte + 1

        return ParsedRangeHeader(
            start_byte=start_byte,
            end_byte=end_byte,
            content_length=content_length,
        )
