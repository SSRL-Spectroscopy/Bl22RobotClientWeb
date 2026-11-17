import { formatClock, formatNumber } from './format'
import type { PvEntry } from './live'
import type { PvDef } from './pvs'

const SEVERITY = ['', 'Minor alarm', 'Major alarm', 'Invalid']

function valueText(entry: PvEntry | undefined): string {
  if (!entry || entry.value === null || entry.value === undefined) return '–'
  return typeof entry.value === 'number' ? formatNumber(entry.value) : String(entry.value)
}

export function Readouts({ pvs, state }: { pvs: PvDef[]; state: Record<string, unknown> }) {
  return (
    <section aria-label="Signals">
      <h2>Signals</h2>
      <table className="readouts">
        <tbody>
          {pvs.map((pv) => {
            const entry = state[pv.key] as PvEntry | undefined
            const connected = entry?.connected ?? false
            const severity = connected ? SEVERITY[entry?.severity ?? 0] : ''
            return (
              <tr key={pv.key} className={connected ? '' : 'stale'} title={pv.name}>
                <th scope="row">{pv.label}</th>
                <td className="value">{valueText(entry)}</td>
                <td className="unit">{pv.unit}</td>
                <td className="note">
                  {!connected && (
                    <>
                      <span aria-hidden="true">○ </span>
                      {entry?.value != null ? `No connection, last ${formatClock(entry.ts)}` : 'No connection'}
                    </>
                  )}
                  {severity && (
                    <>
                      <span aria-hidden="true">▲ </span>
                      {severity}
                    </>
                  )}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </section>
  )
}
