import { useState } from 'react'
import { post } from './auth'
import type { Me } from './auth'
import { formatClock } from './format'
import type { HelmEntry } from './live'

type CrystalEntry = {
  d: number | null
  phi: number | null
  cut: string | null
  read: number | null
  error: string | null
  simulated: boolean
}

type SequenceEntry = { state?: string }

/** The cut a lattice spacing stands for, as the backend decides it. */
function cutOf(d: number): string | null {
  if (Math.abs(d - 3.1356) <= 0.05) return 'Si-111'
  if (Math.abs(d - 1.9201) <= 0.05) return 'Si-220'
  return null
}

/**
 * The monochromator crystal: two stored values that sequences depend on.
 * They are read on request only (Reload) and written by the operator at the
 * helm (Apply). The crystal itself is changed by hand, rarely.
 */
export function Crystal({ me, state }: { me: Me; state: Record<string, unknown> }) {
  const crystal = state['crystal'] as CrystalEntry | undefined
  const helm = state['helm'] as HelmEntry | undefined
  const sequence = state['sequence'] as SequenceEntry | undefined
  const iHold = me.user !== null && helm?.holder === me.user
  const sequenceActive = sequence?.state === 'running' || sequence?.state === 'paused'

  // What the operator has typed; null while the fields follow the values read.
  const [draft, setDraft] = useState<{ d: string; phi: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const d = draft?.d ?? (crystal?.d != null ? String(crystal.d) : '')
  const phi = draft?.phi ?? (crystal?.phi != null ? String(crystal.phi) : '0')
  const typedD = Number(d)
  const typedCut = d !== '' && Number.isFinite(typedD) ? cutOf(typedD) : null

  const run = async (path: string, body?: unknown) => {
    setBusy(true)
    setError('')
    try {
      await post(path, body)
      setDraft(null)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  let why = ''
  if (me.user === null) why = 'Log in as an operator to reload or change these values.'
  else if (!iHold) why = 'Take control to change these values.'
  else if (sequenceActive) why = 'The values cannot be changed while a sequence is running.'

  return (
    <section aria-label="Monochromator crystal">
      <h2>Monochromator crystal</h2>
      <div className="command-panel">
        <p className="crystal-now" role="status">
          {crystal?.cut ? (
            <>
              <strong>{crystal.cut}</strong> · lattice spacing {crystal.d} Å · PHI {crystal.phi}°
            </>
          ) : crystal?.d != null ? (
            <strong>
              <span aria-hidden="true">? </span>Lattice spacing {crystal.d} Å matches no known crystal
            </strong>
          ) : (
            <strong>
              <span aria-hidden="true">? </span>Not known{crystal?.error ? `: ${crystal.error}` : ''}
            </strong>
          )}
          {crystal?.read != null && <span className="hint"> · read at {formatClock(crystal.read)}</span>}
          {crystal?.simulated && <span className="hint"> · simulated values, EPICS is off</span>}
        </p>

        {me.user !== null && (
          <form
            className="command"
            onSubmit={(event) => {
              event.preventDefault()
              run('/api/crystal/apply', { d: typedD, phi: Number(phi) })
            }}
          >
            <label>
              Lattice spacing (Å)
              <input
                type="number"
                step="any"
                min={1}
                max={4}
                value={d}
                disabled={!iHold || sequenceActive}
                onChange={(e) => setDraft({ d: e.target.value, phi })}
              />
            </label>
            <label>
              PHI (°)
              <select
                value={phi}
                disabled={!iHold || sequenceActive}
                onChange={(e) => setDraft({ d, phi: e.target.value })}
              >
                <option value="0">0</option>
                <option value="90">90</option>
              </select>
            </label>
            <span className="hint">{d === '' ? '' : typedCut ? `= ${typedCut}` : 'matches no known crystal'}</span>
            <button
              type="submit"
              className="primary"
              disabled={!iHold || sequenceActive || busy || typedCut === null || draft === null}
              title="Write both values to the PVs"
            >
              Apply
            </button>
            <button
              type="button"
              disabled={busy}
              title="Read both values from the PVs again"
              onClick={() => run('/api/crystal/reload')}
            >
              Reload
            </button>
          </form>
        )}
        {why && <p className="hint">{why}</p>}
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </section>
  )
}
