import { useState } from 'react'
import { call, post } from './auth'
import type { Me } from './auth'
import { formatClock } from './format'
import type { HelmEntry } from './live'

type Measurement = {
  id: string
  position: string
  plate_type: string
  sample_name?: string | null
  number: number
  technique: string
  element: string
  edge: string
  detection: string
  beam: string
  status?: string
  measurable?: boolean
  problem?: string | null
  state?: string
}

type Frame = {
  barcode: string
  container: number | null
  slot: number | null
  owner: string | null
  state: string | null
  locked: boolean
  safe: boolean
  plates: Record<string, string | null>
  measurements: Measurement[]
}

type QueueItem = {
  barcode: string
  container: number
  slot: number
  state: string
  note?: string | null
  measurements: Measurement[]
}
type QueueEntry = { items: QueueItem[] }

function what(m: Measurement): string {
  return `${m.position} · ${m.element} ${m.edge} ${m.technique.toUpperCase()}`
}

function how(m: Measurement): string {
  return `${m.detection.replace('transmission', 'trans').replace('_', ' + ')}, ${m.beam}`
}

/** The kind of measurement, without the position: "Cu K XANES Transmission + SDD, unfocused". */
function kind(m: Measurement): string {
  const detection = m.detection
    .split('_')
    .map((part) => (part === 'transmission' ? 'Transmission' : part.toUpperCase()))
    .join(' + ')
  return `${m.element} ${m.edge} ${m.technique.toUpperCase()} ${detection}, ${m.beam}`
}

// How far a run has got, in words. Nothing is shown for "queued".
const FRAME_STATES: Record<string, string> = {
  scheduled: '○ scheduled',
  loaded: '▶ on the robot',
  done: '● complete',
}
const MEASUREMENT_STATES: Record<string, string> = {
  running: '▶ running',
  uploaded: '▶ uploaded',
  done: '● done',
  failed: '✕ failed',
}

function Queue({
  queue,
  loggedIn,
  canAct,
  setError,
}: {
  queue: QueueEntry | undefined
  loggedIn: boolean
  canAct: boolean
  setError: (message: string) => void
}) {
  const items = queue?.items ?? []

  const run = async (action: () => Promise<unknown>) => {
    setError('')
    try {
      await action()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const move = (index: number, by: number) => {
    const order = items.map((item) => item.barcode)
    const [barcode] = order.splice(index, 1)
    order.splice(index + by, 0, barcode)
    return run(() => post('/api/queue/order', { barcodes: order }))
  }

  const clear = () => {
    if (window.confirm(`Remove all ${items.length} frame${items.length === 1 ? '' : 's'} from the queue?`))
      run(() => post('/api/queue/clear'))
  }

  return (
    <>
      <div className="queue-head">
        <span className="hint">
          {items.length === 0 ? 'The queue is empty.' : `${items.length} frame${items.length === 1 ? '' : 's'} queued.`}
        </span>
        {loggedIn && (
          <span className="queue-actions">
            <button
              type="button"
              disabled={!canAct || items.length === 0}
              title={canAct ? '' : 'Take control to change the queue'}
              onClick={clear}
            >
              Clear the queue
            </button>
          </span>
        )}
      </div>
      <ol className="queue" hidden={items.length === 0}>
        {items.map((item, index) => (
          <li key={item.barcode}>
            <div className="queue-head">
              <strong>{item.barcode}</strong>
              <span className="hint">
                container {item.container}, slot {item.slot} · {item.measurements.length} measurement
                {item.measurements.length === 1 ? '' : 's'}
              </span>
              {FRAME_STATES[item.state] && <span>{FRAME_STATES[item.state]}</span>}
              {item.state === 'verified' && (
                <span>
                  <span aria-hidden="true">● </span>barcode verified
                </span>
              )}
              {item.state === 'mismatch' && (
                <span className="form-error">
                  <span aria-hidden="true">✕ </span>does not match: {item.note}
                </span>
              )}
              {canAct && (
                <span className="queue-actions">
                  <button type="button" disabled={index === 0} onClick={() => move(index, -1)} aria-label="Move up">
                    ↑
                  </button>
                  <button
                    type="button"
                    disabled={index === items.length - 1}
                    onClick={() => move(index, 1)}
                    aria-label="Move down"
                  >
                    ↓
                  </button>
                  <button type="button" onClick={() => run(() => call('DELETE', `/api/queue/frames/${item.barcode}`))}>
                    Remove
                  </button>
                </span>
              )}
            </div>
            <ul>
              {item.measurements.map((m) => (
                <li key={m.id}>
                  {what(m)} <span className="hint">({how(m)})</span>
                  {m.status && m.status !== 'pending' && m.state === 'queued' && (
                    <span> · measure again (portal: {m.status})</span>
                  )}
                  {m.state && MEASUREMENT_STATES[m.state] && <strong> · {MEASUREMENT_STATES[m.state]}</strong>}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </>
  )
}

function FrameCard({
  frame,
  queued,
  canAct,
  setError,
}: {
  frame: Frame
  queued: QueueItem | undefined
  canAct: boolean
  setError: (message: string) => void
}) {
  // Why nothing on this frame can be measured, if so.
  const frameProblem =
    frame.state !== 'internal_storage'
      ? `the portal lists the frame as '${frame.state}', not in internal storage`
      : !frame.safe
        ? 'the frame is not declared safe'
        : !frame.locked
          ? 'the frame is not locked'
          : null
  const problem = (m: Measurement) => frameProblem ?? m.problem ?? (m.measurable === true ? null : 'not measurable')
  // Measurements that are done or failed can be chosen too: they are run again.
  const selectable = (m: Measurement) =>
    problem(m) === null && (m.status === 'pending' || m.status === 'done' || m.status === 'error')
  const pending = frame.measurements.filter((m) => selectable(m) && m.status === 'pending').map((m) => m.id)
  const [chosen, setChosen] = useState<Set<string>>(
    () => new Set(queued ? queued.measurements.map((m) => m.id) : pending),
  )

  const toggle = (id: string) => {
    const next = new Set(chosen)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setChosen(next)
  }

  // The kinds of measurement on this frame that can be queued, in the order
  // they first appear, each with the ids of its measurements.
  const kinds = new Map<string, string[]>()
  for (const m of frame.measurements) {
    if (!selectable(m)) continue
    kinds.set(kind(m), [...(kinds.get(kind(m)) ?? []), m.id])
  }
  const everything = [...kinds.values()].flat()

  // Checks every measurement of one kind, or unchecks them if all are checked.
  const toggleKind = (ids: string[]) => {
    const next = new Set(chosen)
    if (ids.every((id) => next.has(id))) ids.forEach((id) => next.delete(id))
    else ids.forEach((id) => next.add(id))
    setChosen(next)
  }

  const save = async () => {
    setError('')
    try {
      await call('PUT', `/api/queue/frames/${frame.barcode}`, { measurement_ids: [...chosen] })
    } catch (e) {
      setError(`${frame.barcode}: ${(e as Error).message}`)
    }
  }

  const plates = Object.entries(frame.plates)
    .map(([side, plate]) => `${side}: ${plate ?? 'empty'}`)
    .join(', ')

  return (
    <div className="card frame-card">
      <div className="queue-head">
        <strong>{frame.barcode}</strong>
        <span className="hint">
          container {frame.container ?? '?'}, slot {frame.slot ?? '?'} · {frame.owner ?? 'no owner'} · {plates}
        </span>
        <span className="queue-actions">
          {queued && <span className="hint">In the queue</span>}
          <button type="button" className="primary" disabled={!canAct || chosen.size === 0} onClick={save}>
            {queued ? 'Update the queue' : 'Add to the queue'}
          </button>
        </span>
      </div>
      {everything.length > 0 && (
        <div className="select-bar" role="group" aria-label={`Select measurements of ${frame.barcode}`}>
          <span className="hint">Select:</span>
          <button type="button" onClick={() => setChosen(new Set(everything))}>
            All
          </button>
          <button type="button" onClick={() => setChosen(new Set())}>
            None
          </button>
          <button type="button" onClick={() => setChosen(new Set(pending))}>
            Pending only
          </button>
          {[...kinds].map(([label, ids]) => {
            const count = ids.filter((id) => chosen.has(id)).length
            return (
              <button
                key={label}
                type="button"
                className="kind"
                aria-pressed={count === ids.length}
                title={count === ids.length ? 'Uncheck these measurements' : 'Check these measurements'}
                onClick={() => toggleKind(ids)}
              >
                {label}{' '}
                <span className="hint">
                  {count}/{ids.length}
                </span>
              </button>
            )
          })}
        </div>
      )}
      {frameProblem && (
        <p className="frame-problem">
          <span aria-hidden="true">⚠ </span>Cannot be queued: {frameProblem}.
        </p>
      )}
      {frame.measurements.length === 0 && <p className="hint">The portal lists no measurements for this frame.</p>}
      <table className="measurements" hidden={frame.measurements.length === 0}>
        <thead>
          <tr>
            <th>Run</th>
            <th>Position</th>
            <th>Sample</th>
            <th>Measurement</th>
            <th>Detection, beam</th>
            <th>Portal status</th>
          </tr>
        </thead>
        <tbody>
          {frame.measurements.map((m) => (
            <tr key={m.id} className={problem(m) !== null ? 'blocked' : m.status === 'done' ? 'done' : ''}>
              <td>
                <input
                  type="checkbox"
                  aria-label={`Run ${what(m)}`}
                  checked={chosen.has(m.id)}
                  disabled={!selectable(m)}
                  onChange={() => toggle(m.id)}
                />
              </td>
              <td>{m.position}</td>
              <td>{m.sample_name}</td>
              <td>
                {m.element} {m.edge} {m.technique.toUpperCase()} <span className="hint">#{m.number}</span>
              </td>
              <td>{how(m)}</td>
              <td>
                {problem(m) !== null ? (
                  <>
                    <span aria-hidden="true">⚠ </span>cannot measure: {problem(m)}
                  </>
                ) : m.status === 'done' ? (
                  <>
                    <span aria-hidden="true">✓ </span>done
                  </>
                ) : (
                  m.status
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function Samples({ me, state }: { me: Me; state: Record<string, unknown> }) {
  const [frames, setFrames] = useState<Frame[] | null>(null)
  const [fetched, setFetched] = useState(0)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const helm = state['helm'] as HelmEntry | undefined
  const queue = state['queue'] as QueueEntry | undefined
  const iHold = me.user !== null && helm?.holder === me.user

  const load = async () => {
    setLoading(true)
    setError('')
    try {
      const body = (await call('GET', '/api/samples')) as { frames: Frame[]; fetched: number }
      setFrames(body.frames)
      setFetched(body.fetched)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <section aria-label="Measurement queue">
      <h2>Measurement queue</h2>
      <div className="command-panel">
        <Queue queue={queue} loggedIn={me.user !== null} canAct={iHold} setError={setError} />
      </div>

      {me.user !== null && (
        <div className="samples">
          <div className="plot-head">
            <h2>Frames in internal storage</h2>
            <span className="queue-actions">
              {frames && <span className="hint">read at {formatClock(fetched)}</span>}
              <button type="button" disabled={loading} onClick={load}>
                {loading ? 'Reading…' : frames ? 'Read again' : 'Read from the portal'}
              </button>
            </span>
          </div>
          {!iHold && frames && <p className="hint">Take control to change the queue.</p>}
          {frames && frames.length === 0 && <p className="hint">The portal lists no frames in internal storage.</p>}
          {frames?.map((frame) => (
            <FrameCard
              // re-create the card when the list is read again or the queued selection changes
              key={`${frame.barcode}-${fetched}`}
              frame={frame}
              queued={queue?.items.find((item) => item.barcode === frame.barcode)}
              canAct={iHold}
              setError={setError}
            />
          ))}
        </div>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </section>
  )
}
