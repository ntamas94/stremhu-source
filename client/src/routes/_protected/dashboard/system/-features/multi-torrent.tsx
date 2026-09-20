import { useSuspenseQuery } from '@tanstack/react-query'
import { toast } from 'sonner'

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/shared/components/ui/card'
import { Label } from '@/shared/components/ui/label'
import { Switch } from '@/shared/components/ui/switch'
import { parseApiError } from '@/shared/lib/utils'
import {
  getSystemSettings,
  useSystemSettingsUpdate,
} from '@/shared/queries/system'

export function MultiTorrent() {
  const { data: systemSetting } = useSuspenseQuery(getSystemSettings)
  const { mutateAsync: updateSetting, isPending } = useSystemSettingsUpdate()

  const handleChange = async (checked: boolean) => {
    try {
      await updateSetting({ multiTorrent: checked })
    } catch (error) {
      toast.error(parseApiError(error))
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Multi torrent (kísérleti)</CardTitle>
        <CardDescription>
          Ha ugyanaz a release több trackeren is fent van, a találati listában
          egy sorba kerülnek, összeadott seederszámmal, a többi tracker pedig
          tartalék forrás lesz. Ha az info hash eltér, a példányok külön
          torrentként indulnak, és a kész darabokat megosztják egymással.
          Kikapcsolva minden tracker külön sor.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <div className="grid gap-1">
          <Label htmlFor="multiTorrent" className="flex items-start gap-3">
            <p className="flex-1 text-sm leading-none font-medium">
              Multi torrent
            </p>
            <Switch
              id="multiTorrent"
              checked={systemSetting.multiTorrent}
              disabled={isPending}
              onCheckedChange={handleChange}
            />
          </Label>
          <p className="text-muted-foreground text-sm">
            Minden érintett trackeren külön torrent fut, így több hálózati
            kapcsolatot és memóriát használ.
          </p>
        </div>
      </CardContent>
    </Card>
  )
}
