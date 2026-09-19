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

export function DualSwarm() {
  const { data: systemSetting } = useSuspenseQuery(getSystemSettings)
  const { mutateAsync: updateSetting, isPending } = useSystemSettingsUpdate()

  const handleChange = async (checked: boolean) => {
    try {
      await updateSetting({ dualSwarm: checked })
    } catch (error) {
      toast.error(parseApiError(error))
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Dupla swarm (kísérleti)</CardTitle>
        <CardDescription>
          Ha ugyanaz a release több trackeren más info hash-sel van fent, a
          példányok külön torrentként indulnak, és a kész darabokat megosztják
          egymással.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Label htmlFor="dualSwarm" className="flex items-start gap-3">
          <div className="grid gap-1">
            <p className="flex-1 text-sm leading-none font-medium">
              Azonos release együtt töltése
            </p>
            <p className="text-muted-foreground text-sm">
              Minden érintett trackeren külön torrent fut, így több hálózati
              kapcsolatot és memóriát használ.
            </p>
          </div>
          <Switch
            id="dualSwarm"
            checked={systemSetting.dualSwarm}
            disabled={isPending}
            onCheckedChange={handleChange}
          />
        </Label>
      </CardContent>
    </Card>
  )
}
