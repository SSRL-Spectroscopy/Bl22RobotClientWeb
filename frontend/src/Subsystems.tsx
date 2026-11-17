import { formatClock } from './format'
import type { SubsystemEntry } from './live'

const SUBSYSTEMS = ['MOTOR', 'SAMPLE', 'BEAMLINE', 'SCAN'] as const

type Kind = 'offline' | 'failed' | 'busy' | 'idle'

function describe(entry: SubsystemEntry | undefined): { kind: Kind; icon: string; label: string; detail: string } {
  if (!entry || !entry.connected) {
    const last = entry?.state ? `Last seen: ${entry.state} at ${formatClock(entry.ts)}` : 'No status received yet'
    return { kind: 'offline', icon: '○', label: 'No connection', detail: last }
  }
  if (entry.failed) {
    return { kind: 'failed', icon: '✕', label: 'Error', detail: `${entry.state ?? ''} · ${entry.error ?? ''}` }
  }
  if (entry.state !== 'IDLE') {
    return { kind: 'busy', icon: '▶', label: 'Running', detail: entry.state ?? '' }
  }
  return { kind: 'idle', icon: '●', label: 'Idle', detail: 'No error' }
}

export function Subsystems({ state }: { state: Record<string, unknown> }) {
  return (
    <section aria-label="Subsystems">
      <h2>Subsystems</h2>
      <div className="cards">
        {SUBSYSTEMS.map((name) => {
          const info = describe(state[`subsystem.${name}`] as SubsystemEntry | undefined)
          return (
            <div key={name} className={`card status-${info.kind}`}>
              <div className="card-title">{name}</div>
              <div className="card-status">
                <span className="status-icon" aria-hidden="true">
                  {info.icon}
                </span>
                {info.label}
              </div>
              <div className="card-detail">{info.detail}</div>
            </div>
          )
        })}
      </div>
    </section>
  )
}
