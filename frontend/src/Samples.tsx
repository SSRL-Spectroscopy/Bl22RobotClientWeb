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
  /** the reference foil the measurement needs (queue only) */
  reference?: string | null
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

type ReferencesEntry = { available: string[] | null; error: string | null; ts: number }

function Queue({
  queue,
  references,
  loadedReference,
  loggedIn,
  canAct,
  setError,
}: {
  queue: QueueEntry | undefined
  references: ReferencesEntry | undefined
  /** the reference foil that is loaded now; it counts as available */
  loadedReference: string | null
  loggedIn: boolean
  canAct: boolean
  setError: (message: string) => void
}) {
  const items = queue?.items ?? []
  const [reading, setReading] = useState(false)

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

  const readReferences = async () => {
    setReading(true)
    await run(() => post('/api/references/read'))
    setReading(false)
  }

  // Which foils the instrument has. null: not known, then nothing is marked.
  const available =
    references?.available == null
      ? null
      : new Set([...references.available, ...(loadedReference ? [loadedReference] : [])].map((n) => n.toUpperCase()))
  const isMissing = (m: Measurement) =>
    available !== null && m.reference != null && !available.has(m.reference.toUpperCase())
  const needed = [...new Set(items.flatMap((item) => item.measurements.map((m) => m.reference ?? '')))].filter(Boolean)
  const missing = needed.filter((name) => available !== null && !available.has(name.toUpperCase()))
  const blocked = items.reduce((n, item) => n + item.measurements.filter(isMissing).length, 0)
  const total = items.reduce((n, item) => n + item.measurements.length, 0)

  return (
    <>
      <div className="queue-head">
        <span className="hint">
          {items.length === 0
            ? 'The queue is empty.'
            : `${items.length} frame${items.length === 1 ? '' : 's'}, ${total} measurement${total === 1 ? '' : 's'} queued.`}
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

      <div className="queue-head queue-references">
        <span>
          Reference foils needed: <strong>{needed.length ? needed.join(', ') : 'none'}</strong>
          {available === null && (
            <span className="hint">
              {' · '}not checked: what the instrument has is not known
              {references?.error ? ` (${references.error})` : ''}
            </span>
          )}
        </span>
        {loggedIn && (
          <span className="queue-actions">
            <button type="button" disabled={reading} onClick={readReferences}>
              {reading ? 'Reading…' : 'Read references'}
            </button>
          </span>
        )}
      </div>
      {missing.length > 0 && (
        <p className="frame-problem" role="status">
          <span aria-hidden="true">⚠ </span>
          {blocked} measurement{blocked === 1 ? '' : 's'} cannot run: the instrument has no reference foil for{' '}
          <strong>{missing.join(', ')}</strong>. The measurement sequence will not start until they are removed or
          the foil is there.
        </p>
      )}

      <table className="queue-table" hidden={items.length === 0}>
        <thead>
          <tr>
            <th>Pos.</th>
            <th>Edge</th>
            <th>Technique</th>
            <th>Detection</th>
            <th>Beam</th>
            <th>Ref.</th>
            <th>State</th>
            <th>
              <span className="visually-hidden">Remove</span>
            </th>
          </tr>
        </thead>
        {items.map((item, index) => (
          <tbody key={item.barcode}>
            <tr className="queue-frame">
              <th colSpan={8} scope="rowgroup">
                <span className="queue-head">
                  <span>
                    {index + 1}. <strong>{item.barcode}</strong>
                  </span>
                  <span className="hint">
                    container {item.container}, slot {item.slot}
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
                      <button
                        type="button"
                        className="icon"
                        disabled={index === 0}
                        onClick={() => move(index, -1)}
                        aria-label={`Move frame ${item.barcode} up`}
                        title="Move up"
                      >
                        ↑
                      </button>
                      <button
                        type="button"
                        className="icon"
                        disabled={index === items.length - 1}
                        onClick={() => move(index, 1)}
                        aria-label={`Move frame ${item.barcode} down`}
                        title="Move down"
                      >
                        ↓
                      </button>
                      <button
                        type="button"
                        className="icon"
                        onClick={() => run(() => call('DELETE', `/api/queue/frames/${item.barcode}`))}
                        aria-label={`Remove frame ${item.barcode} and all its measurements from the queue`}
                        title="Remove the whole frame"
                      >
                        <TrashIcon />
                      </button>
                    </span>
                  )}
                </span>
              </th>
            </tr>
            {item.measurements.map((m) => {
              const noFoil = isMissing(m)
              const again = m.status && m.status !== 'pending' && m.state === 'queued'
              return (
                <tr key={m.id} className={noFoil ? 'blocked' : m.state === 'done' ? 'done' : ''}>
                  <td>{m.position}</td>
                  <td>
                    {m.element} {m.edge}
                  </td>
                  <td>{m.technique.toUpperCase()}</td>
                  <td>{m.detection.replace('transmission', 'trans').replace('_', ' + ')}</td>
                  <td>{m.beam}</td>
                  <td>
                    {noFoil && <span aria-hidden="true">⚠ </span>}
                    {m.reference ?? '?'}
                    {noFoil && <strong> missing</strong>}
                  </td>
                  <td>
                    {m.state && MEASUREMENT_STATES[m.state] ? <strong>{MEASUREMENT_STATES[m.state]}</strong> : null}
                    {again ? `again (portal: ${m.status})` : null}
                  </td>
                  <td className="queue-remove">
                    {canAct && (
                      <button
                        type="button"
                        className="icon"
                        onClick={() =>
                          run(() => call('DELETE', `/api/queue/frames/${item.barcode}/measurements/${m.id}`))
                        }
                        aria-label={`Remove ${what(m)} from the queue`}
                        title="Remove this measurement"
                      >
                        <TrashIcon />
                      </button>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        ))}
      </table>
    </>
  )
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true" fill="none" stroke="currentColor" strokeWidth="1.4">
      <path d="M2.5 4h11M6 4V2.5h4V4M4 4l.7 9.5h6.6L12 4M6.6 6.5v4.5M9.4 6.5v4.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
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
  const references = state['references'] as ReferencesEntry | undefined
  const equipment = state['equipment'] as { items?: Record<string, { value?: unknown }> } | undefined
  const loaded = equipment?.items?.['reference']?.value
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
    <div className="tab-stack">
      {error && (
        <p className="error-banner" role="alert">
          <span aria-hidden="true">✕</span>
          <span>{error}</span>
          <button type="button" onClick={() => setError('')}>
            Dismiss
          </button>
        </p>
      )}
    <div className="columns">
      <section className="column" aria-label="Frames in internal storage">
        <div className="plot-head">
          <h2>Frames in internal storage</h2>
          {me.user !== null && (
            <span className="queue-actions">
              {frames && <span className="hint">read at {formatClock(fetched)}</span>}
              <button type="button" disabled={loading} onClick={load}>
                {loading ? 'Reading…' : frames ? 'Read again' : 'Read from the portal'}
              </button>
            </span>
          )}
        </div>
        <div className="column-scroll">
          {me.user === null && <p className="hint">Log in as an operator to read the frames from the portal.</p>}
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
      </section>
      <section className="column" aria-label="Measurement queue">
        <div className="plot-head">
          <h2>Measurement queue</h2>
        </div>
        <div className="column-scroll">
          <div className="command-panel">
            <Queue
              queue={queue}
              references={references}
              loadedReference={typeof loaded === 'string' ? loaded : null}
              loggedIn={me.user !== null}
              canAct={iHold}
              setError={setError}
            />
          </div>
        </div>
      </section>
    </div>
    </div>
  )
}
