import { useEffect, useState } from 'react'

export type PvDef = {
  name: string
  key: string
  label: string
  unit: string
  plot: boolean
  enum: boolean
  /** the plot it is drawn in: 'adc' or 'mono' */
  group: string
  /** false for the inputs of a value the backend works out; listed in the Signals table only */
  show: boolean
  /** show the value in exponential form, such as 1e+9 */
  exp?: boolean
}

/** The PV list from the backend. Retries until the backend answers. */
export function usePvDefs(): PvDef[] {
  const [pvs, setPvs] = useState<PvDef[]>([])

  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const load = async () => {
      try {
        const res = await fetch('/api/pvs')
        if (!res.ok) throw new Error(String(res.status))
        const body: PvDef[] = await res.json()
        if (!cancelled) setPvs(body)
      } catch {
        if (!cancelled) timer = setTimeout(load, 2000)
      }
    }
    load()
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [])

  return pvs
}
