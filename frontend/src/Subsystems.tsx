import { formatClock } from './format'
import type { SubsystemEntry } from './live'

const SUBSYSTEMS = ['MOTOR', 'SAMPLE', 'BEAMLINE', 'SCAN'] as const

type Kind = 'offline' | 'failed' | 'busy' | 'idle'

function describe(entry: SubsystemEntry | undefined): { kind: Kind; icon: string; label: string; detail: string } {
  if (!entry || !entry.connected) {
    const last = entry?.state ? `Last seen: ${entry.state} at ${formatClock(entry.ts)}` : 'No status received yet'
    return { kind: 'offline', icon: '○', label: 'no connection', detail: last }
  }
  if (entry.failed) {
    return { kind: 'failed', icon: '✕', label: 'error', detail: `${entry.state ?? ''} · ${entry.error ?? ''}` }
  }
  if (entry.state !== 'IDLE') {
    return { kind: 'busy', icon: '▶', label: 'running', detail: entry.state ?? '' }
  }
  return { kind: 'idle', icon: '●', label: 'idle', detail: 'No error' }
}

/** One small pill per subsystem of the control server, for the top bar. Hover for the detail. */
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
