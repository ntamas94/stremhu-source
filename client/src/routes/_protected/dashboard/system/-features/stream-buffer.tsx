import { useForm } from '@tanstack/react-form'
import { useSuspenseQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { toast } from 'sonner'
import * as z from 'zod'

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/shared/components/ui/card'
import { Field, FieldError, FieldLabel } from '@/shared/components/ui/field'
import {
  InputGroup,
  InputGroupAddon,
  InputGroupInput,
  InputGroupText,
} from '@/shared/components/ui/input-group'
import { parseApiError } from '@/shared/lib/utils'
import { getRelaySettings, useUpdateRelaySetting } from '@/shared/queries/relay'

const schema = z.object({
  streamBufferSeconds: z.coerce
    .number<string>('Csak szám adható meg')
    .int('Csak egész szám adható meg')
    .min(0, 'Legalább 0')
    .max(120, 'Legfeljebb 120'),
})

export function StreamBuffer() {
  const { data: relaySettings } = useSuspenseQuery(getRelaySettings)

  const { mutateAsync: updateSetting } = useUpdateRelaySetting()

  const setting = useMemo(() => {
    return {
      streamBufferSeconds: relaySettings.streamBufferSeconds.toString(),
    }
  }, [relaySettings])

  const form = useForm({
    defaultValues: {
      streamBufferSeconds: setting.streamBufferSeconds,
    },
    validators: {
      onChange: schema,
    },
    listeners: {
      onChangeDebounceMs: 1000,
      onChange: ({ formApi }) => {
        if (formApi.state.isValid) {
          formApi.handleSubmit()
        }
      },
    },
    onSubmit: async ({ value, formApi }) => {
      try {
        await updateSetting({
          streamBufferSeconds: Number(value.streamBufferSeconds),
        })
      } catch (error) {
        formApi.reset()
        const message = parseApiError(error)
        toast.error(message)
      }
    },
  })

  return (
    <Card>
      <CardHeader>
        <CardTitle>Lejátszási puffer</CardTitle>
        <CardDescription>
          Ennyi másodpercnyi adatot tölt előre a lejátszás előtt, a stream mért
          sebessége alapján. 10–30 másodperc ajánlott, 0 esetén csak a minimális
          előtöltés marad.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-6">
        <form.Field name="streamBufferSeconds">
          {(field) => (
            <Field>
              <FieldLabel>Puffer mérete</FieldLabel>
              <InputGroup>
                <InputGroupInput
                  inputMode="numeric"
                  id={field.name}
                  name={field.name}
                  value={field.state.value}
                  onBlur={field.handleBlur}
                  onChange={(e) => {
                    field.handleChange(e.target.value)
                  }}
                />
                <InputGroupAddon align="inline-end">
                  <InputGroupText>másodperc</InputGroupText>
                </InputGroupAddon>
              </InputGroup>
              {field.state.meta.isTouched && (
                <FieldError errors={field.state.meta.errors} />
              )}
            </Field>
          )}
        </form.Field>
      </CardContent>
    </Card>
  )
}
