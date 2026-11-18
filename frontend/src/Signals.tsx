import { formatClock } from './format'
import type { PvDef } from './pvs'

type PvEntry = { connected: boolean; value: unknown; severity?: number; ts?: number }

function formatValue(value: unknown, exponential: boolean): string {
  if (value === null || value === undefined) return '–'
  if (typeof value === 'number') {
    // amplifier gains: 1e+9, 5e+8
    if (exponential) return Number(value.toPrecision(4)).toExponential()
    // Whole numbers (encoder counts) are shown exactly; their width does not change.
    if (Number.isInteger(value)) return String(value)
    // Four significant digits, so the last digits of a noisy value do not make
    // the column jump. Very small or large values would come out as 1.234e-7:
    // write those as plain numbers.
    const rounded = value.toPrecision(4)
    return rounded.includes('e') ? String(Number(rounded)) : rounded
  }
  return String(value)
}

/** Every value the backend monitors, as a table. */
export function Signals({ pvs, state }: { pvs: PvDef[]; state: Record<string, unknown> }) {
  return (
    <section aria-label="Signals">
      <h2>Signals</h2>
      <table className="readouts signals">
        <thead>
          <tr>
            <th>Signal</th>
            <th className="number">Value</th>
            <th>Unit</th>
            <th>State</th>
            <th>Updated</th>
            <th>Name</th>
          </tr>
        </thead>
        <tbody>
          {pvs.map((pv) => {
            const entry = state[pv.key] as PvEntry | undefined
            const connected = entry?.connected === true
            return (
              <tr key={pv.key} className={connected ? '' : 'stale'}>
                <th scope="row">{pv.label}</th>
                <td className="number">{formatValue(entry?.value, pv.exp === true)}</td>
                <td>{pv.unit}</td>
                <td>
                  <span aria-hidden="true">{connected ? '● ' : '○ '}</span>
                  {connected ? 'connected' : entry?.value == null ? 'no value yet' : 'not connected, last value'}
                </td>
                <td>{entry?.ts ? formatClock(entry.ts) : ''}</td>
                <td className="hint">{pv.name === 'mono.energy' ? 'worked out by the backend' : pv.name}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}
