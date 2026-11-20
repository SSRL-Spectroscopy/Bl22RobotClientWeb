import { formatWhen } from './format'
import type { SubsystemEntry } from './live'

const SUBSYSTEMS = ['MOTOR', 'SAMPLE', 'BEAMLINE', 'SCAN'] as const

type Kind = 'offline' | 'failed' | 'busy' | 'idle'

// The control server is not polled: a status is read only when a command
// needs it. So every pill shows the last status read, and says when.
function describe(entry: SubsystemEntry | undefined): { kind: Kind; icon: string; label: string; detail: string } {
  if (!entry || !entry.state) {
    return { kind: 'offline', icon: '○', label: 'not read yet', detail: 'The status is read when a command is sent.' }
  }
  const read = `Read at ${formatWhen(entry.ts)}.`
  if (entry.failed) {
    return { kind: 'failed', icon: '✕', label: 'error', detail: `${entry.state} · ${entry.error ?? ''}. ${read}` }
  }
  if (entry.state !== 'IDLE') {
    return { kind: 'busy', icon: '▶', label: 'running', detail: `${entry.state}. ${read}` }
  }
  return { kind: 'idle', icon: '●', label: 'idle', detail: `No error. ${read}` }
}

/** One small pill per subsystem of the control server, for the top bar: the
 *  status as last read. Hover for the detail and the time. */
export function Subsystems({ state }: { state: Record<string, unknown> }) {
  return (
    <div className="subsystems" role="group" aria-label="Subsystems">
      {SUBSYSTEMS.map((name) => {
        const info = describe(state[`subsystem.${name}`] as SubsystemEntry | undefined)
        return (
          <span key={name} className={`pill status-${info.kind}`} title={`${name}: ${info.label}. ${info.detail}`}>
            <span className="status-icon" aria-hidden="true">
              {info.icon}
            </span>
            {name}
            <span className="visually-hidden"> {info.label}</span>
            {info.kind === 'failed' && <span> error</span>}
          </span>
        )
      })}
    </div>
  )
}
