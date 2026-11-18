import { useEffect, useState } from 'react'
import { post } from './auth'
import type { Me } from './auth'
import { formatClock } from './format'
import type { HelmEntry } from './live'

type ParamDef = { name: string; label: string; kind: 'int' | 'name'; min: number | null; max: number | null }
type CommandDef = { name: string; label: string; group: string; subsystem: string; params: ParamDef[] }
type QueryDef = { name: string; label: string }
type Catalogue = { motion_enabled: boolean; commands: CommandDef[]; queries: QueryDef[] }

type Frame = { container: number; slot: number }
type ItemRecord = { status: 'known' | 'changing' | 'unknown'; value: unknown; reason: string | null }
type EquipmentEntry = { items: Record<string, ItemRecord> }

type CommandEntry = {
  phase: 'none' | 'starting' | 'running' | 'succeeded' | 'failed'
  label?: string
  text?: string | null
  user?: string
  step?: string | null
  detail?: string | null
  finished?: number | null
}

type LinesEntry = { lines: string[]; ts: number }

const ITEMS: { key: string; label: string }[] = [
  { key: 'frame', label: 'Frame held by the robot' },
  { key: 'reference', label: 'Reference foil' },
  { key: 'filter', label: 'FY filter' },
]

function useCatalogue(): Catalogue | null {
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null)
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const load = async () => {
      try {
        const response = await fetch('/api/commands')
        if (!response.ok) throw new Error(String(response.status))
        const body: Catalogue = await response.json()
        if (!cancelled) setCatalogue(body)
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
  return catalogue
}

function valueText(key: string, value: unknown): string {
  if (value === null || value === undefined) return 'Nothing'
  if (key === 'frame') {
    const frame = value as Frame
    return `Container ${frame.container}, slot ${frame.slot}`
  }
  return String(value)
}

/** Lets the operator at the helm state what is actually loaded. */
function Declare({ item, onDone }: { item: string; onDone: (error: string) => void }) {
  const [container, setContainer] = useState('0')
  const [slot, setSlot] = useState('0')
  const [name, setName] = useState('')

  const send = async (value: unknown) => {
    try {
      await post('/api/equipment/declare', { item, value })
      onDone('')
    } catch (e) {
      onDone((e as Error).message)
    }
  }

  return (
    <div className="declare">
      <button type="button" onClick={() => send(null)}>
        Nothing is loaded
      </button>
      <span className="hint">or loaded:</span>
      {item === 'frame' ? (
        <>
          <label>
            Container
            <input type="number" min={0} max={2} value={container} onChange={(e) => setContainer(e.target.value)} />
          </label>
          <label>
            Slot
            <input type="number" min={0} max={11} value={slot} onChange={(e) => setSlot(e.target.value)} />
          </label>
          <button type="button" onClick={() => send({ container: Number(container), slot: Number(slot) })}>
            Declare
          </button>
        </>
      ) : (
        <>
          <label>
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <button type="button" disabled={!name} onClick={() => send(name)}>
            Declare
          </button>
        </>
      )}
    </div>
  )
}

function Equipment({
  equipment,
  canAct,
  setError,
}: {
  equipment: EquipmentEntry | undefined
  canAct: boolean
  setError: (message: string) => void
}) {
  const [open, setOpen] = useState<string | null>(null)
  return (
    <table className="readouts equipment">
      <tbody>
        {ITEMS.map(({ key, label }) => {
          const record = equipment?.items[key]
          const status = record?.status ?? 'unknown'
          return (
            <tr key={key} className={status === 'known' ? '' : 'stale'}>
              <th scope="row">{label}</th>
              <td>
                {status === 'known' && <strong>{valueText(key, record?.value)}</strong>}
                {status === 'changing' && (
                  <>
                    <span aria-hidden="true">▶ </span>Changing…
                  </>
                )}
                {status === 'unknown' && (
                  <>
                    <strong>
                      <span aria-hidden="true">? </span>Unknown
                    </strong>
                    {record?.reason && <div className="note">{record.reason}</div>}
                  </>
                )}
                {open === key && canAct && (
                  <Declare
                    item={key}
                    onDone={(error) => {
                      setError(error)
                      if (!error) setOpen(null)
                    }}
                  />
                )}
              </td>
              <td className="actions">
                {canAct && status !== 'changing' && (
                  <button type="button" onClick={() => setOpen(open === key ? null : key)}>
                    {open === key ? 'Close' : 'Declare…'}
                  </button>
                )}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function CommandForm({
  command,
  disabled,
  setError,
}: {
  command: CommandDef
  disabled: boolean
  setError: (message: string) => void
}) {
  const [values, setValues] = useState<Record<string, string>>({})
  const [sending, setSending] = useState(false)

  const send = async () => {
    const args: Record<string, unknown> = {}
    for (const param of command.params) {
      const raw = values[param.name] ?? ''
      args[param.name] = param.kind === 'int' ? Number(raw) : raw
    }
    setSending(true)
    setError('')
    try {
      await post(`/api/commands/${command.name}`, { args })
    } catch (e) {
      setError(`${command.label}: ${(e as Error).message}`)
    } finally {
      setSending(false)
    }
  }

  const incomplete = command.params.some((param) => (values[param.name] ?? '') === '')
  return (
    <form
      className="command"
      onSubmit={(event) => {
        event.preventDefault()
        send()
      }}
    >
      {command.params.map((param) => (
        <label key={param.name}>
          {param.label}
          <input
            type={param.kind === 'int' ? 'number' : 'text'}
            min={param.min ?? undefined}
            max={param.max ?? undefined}
            value={values[param.name] ?? ''}
            disabled={disabled}
            onChange={(e) => setValues({ ...values, [param.name]: e.target.value })}
          />
        </label>
      ))}
      <button type="submit" disabled={disabled || sending || incomplete}>
        {command.label}
      </button>
    </form>
  )
}

function CommandStatus({ command }: { command: CommandEntry | undefined }) {
  if (!command || command.phase === 'none') return <p className="hint">No command has been sent yet.</p>
  const running = command.phase === 'starting' || command.phase === 'running'
  const icon = running ? '▶' : command.phase === 'succeeded' ? '●' : '✕'
  const word = running ? 'Running' : command.phase === 'succeeded' ? 'Finished' : 'Failed'
  return (
    <p className={`command-status phase-${running ? 'running' : command.phase}`} role="status">
      <span className="status-icon" aria-hidden="true">
        {icon}
      </span>
      <strong>{word}:</strong> {command.label}
      {command.text && <code> {command.text}</code>} by {command.user}
      {running && command.step && <> · step {command.step}</>}
      {!running && command.finished && <> · {formatClock(command.finished)}</>}
      {command.detail && <span className="command-detail">{command.detail}</span>}
    </p>
  )
}

function Queries({ queries, state }: { queries: QueryDef[]; state: Record<string, unknown> }) {
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const slots = state['storage_slots'] as LinesEntry | undefined
  const motors = state['motors'] as { positions: Record<string, number>; ts: number } | undefined

  const ask = async (query: QueryDef) => {
    try {
      const body = (await post(`/api/queries/${query.name}`)) as { lines: string[] }
      setAnswers((old) => ({ ...old, [query.name]: body.lines.join(' | ') || '(no data)' }))
    } catch (e) {
      setAnswers((old) => ({ ...old, [query.name]: `Error: ${(e as Error).message}` }))
    }
  }

  return (
    <div className="queries">
      {queries.map((query) => (
        <div key={query.name} className="query">
          <button type="button" onClick={() => ask(query)}>
            Read {query.label.toLowerCase()}
          </button>
          {query.name !== 'storage_slots' && answers[query.name] && <span>{answers[query.name]}</span>}
          {query.name === 'storage_slots' && answers[query.name]?.startsWith('Error') && (
            <span>{answers[query.name]}</span>
          )}
        </div>
      ))}
      <div className="query">
        <button
          type="button"
          onClick={() =>
            post('/api/motors/read')
              .then(() => setAnswers((old) => ({ ...old, motors: '' })))
              .catch((e: Error) => setAnswers((old) => ({ ...old, motors: `Error: ${e.message}` })))
          }
        >
          Read motor positions
        </button>
        {answers['motors'] && <span>{answers['motors']}</span>}
      </div>
      {motors && (
        <div className="motors">
          <div className="hint">Motor positions and robot joints read at {formatClock(motors.ts)}</div>
          <table>
            <tbody>
              {Object.entries(motors.positions)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([name, position]) => (
                  <tr key={name}>
                    <td>{name}</td>
                    <td>{position.toFixed(3)}</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}
      {slots && (
        <div className="slots">
          <div className="hint">Storage slots as reported by the control server at {formatClock(slots.ts)}</div>
          {slots.lines.map((line, container) => (
            <div key={container} className="slot-row">
              <span className="slot-label">Container {container}</span>
              {line.split(/\s+/).map((flag, slot) => (
                <span key={slot} className="slot" title={`Container ${container}, slot ${slot}`}>
                  {flag}
                </span>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** What the backend believes is loaded, and the controls to declare it. On the Settings page. */
export function SampleHandling({ me, state }: { me: Me; state: Record<string, unknown> }) {
  const [error, setError] = useState('')
  const helm = state['helm'] as HelmEntry | undefined
  const command = state['command'] as CommandEntry | undefined
  const equipment = state['equipment'] as EquipmentEntry | undefined
  const iHold = me.user !== null && helm?.holder === me.user
  const running = command?.phase === 'starting' || command?.phase === 'running'
  return (
    <section aria-label="Sample handling">
      <h2>Sample handling</h2>
      <Equipment equipment={equipment} canAct={iHold && !running} setError={setError} />
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </section>
  )
}

/** Single commands and read-outs, sent by hand. */
export function ManualControl({ me, state }: { me: Me; state: Record<string, unknown> }) {
  const catalogue = useCatalogue()
  const [error, setError] = useState('')
  const helm = state['helm'] as HelmEntry | undefined
  const command = state['command'] as CommandEntry | undefined

  const iHold = me.user !== null && helm?.holder === me.user
  const running = command?.phase === 'starting' || command?.phase === 'running'
  const motion = catalogue?.motion_enabled ?? false

  let why = ''
  if (!motion) why = 'Commands that move hardware are switched off in the backend configuration.'
  else if (me.user === null) why = 'Log in as an operator and take control to send commands.'
  else if (!iHold) why = 'Take control to send commands.'

  return (
    <section aria-label="Manual control">
      <h2>Manual control</h2>
      <div className="command-panel">
        <CommandStatus command={command} />
        {why && <p className="hint">{why}</p>}
        <div className="command-list">
          {catalogue?.commands.map((definition) => (
            <CommandForm
              key={definition.name}
              command={definition}
              disabled={!iHold || !motion || running}
              setError={setError}
            />
          ))}
        </div>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </div>

      {me.user !== null && catalogue && <Queries queries={catalogue.queries} state={state} />}
    </section>
  )
}
