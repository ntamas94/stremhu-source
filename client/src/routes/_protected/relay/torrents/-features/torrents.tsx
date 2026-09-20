import { useSuspenseQuery } from '@tanstack/react-query'
import { CircleCheckBigIcon, InfoIcon } from 'lucide-react'

import {
  Alert,
  AlertDescription,
  AlertTitle,
} from '@/shared/components/ui/alert'
import { getTorrents } from '@/shared/queries/torrents'

import { Torrent } from '../-components/torrent'

export function Torrents() {
  const { data: torrents } = useSuspenseQuery(getTorrents)

  if (torrents.length === 0) {
    return (
      <Alert>
        <CircleCheckBigIcon />
        <AlertTitle>Nincs aktív torrent</AlertTitle>
        <AlertDescription>
          Az elindított médiákhoz tartozó torrentek itt fognak megjelenni.
        </AlertDescription>
      </Alert>
    )
  }

  // Azonos release több trackerről (multi torrent): egy kártya, egymás alatt.
  const groups = new Map<string, typeof torrents>()
  for (const torrent of torrents) {
    groups.set(torrent.name, [...(groups.get(torrent.name) ?? []), torrent])
  }

  return (
    <div className="grid gap-4">
      {[...groups.values()].map((group) => (
        <div
          key={group[0].infoHash}
          className="grid gap-4 divide-y rounded-md bg-muted/50 p-4 [&>*:not(:last-child)]:pb-4"
        >
          {group.map((torrent) => (
            <Torrent key={torrent.infoHash} torrent={torrent} />
          ))}
        </div>
      ))}
      <Alert>
        <InfoIcon />
        <AlertTitle>Torrentek alatt látható értékek jelentése</AlertTitle>
        <AlertDescription>
          letöltött adat | letöltési sebesség | feltöltött adat | feltöltési
          sebesség | torrent teljes mérete
        </AlertDescription>
      </Alert>
    </div>
  )
}
