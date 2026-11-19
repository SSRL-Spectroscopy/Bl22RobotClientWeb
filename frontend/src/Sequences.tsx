import { useEffect, useRef, useState } from 'react'
import { call, post } from './auth'
import type { Me } from './auth'
import { formatClock, formatWhen } from './format'
import type { HelmEntry } from './live'
import { StepTree } from './StepTree'
import type { StepNode } from './StepTree'

type SequenceDef = { name: string; label: string; description: string }

type LogLine = { ts: number; level: string; text: string }

type SequenceEntry = {
  state: 'idle' | 'running' | 'paused' | 'finished' | 'aborted' | 'failed'
  label?: string
  user?: string
  step?: string | null
  detail?: string | null
  waiting?: string | null
  started?: number
  finished?: number | null
  pause_requested?: boolean
  abort_requested?: boolean
  log?: LogLine[]
}

function useSequenceDefs(): SequenceDef[] {
  const [defs, setDefs] = useState<SequenceDef[]>([])
  useEffect(() => {
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined
    const load = async () => {
      try {
        const body = (await call('GET', '/api/sequences')) as SequenceDef[]
        if (!cancelled) setDefs(body)
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
  return defs
}

function statusLine(sequence: SequenceEntry): { icon: string; kind: string; text: string } {
  const name = sequence.label ?? 'Sequence'
  switch (sequence.state) {
    case 'running':
      if (sequence.abort_requested) return { icon: '▶', kind: 'running', text: `${name}: stopping after the current command` }
      if (sequence.pause_requested) return { icon: '▶', kind: 'running', text: `${name}: pausing after the current command` }
      if (sequence.waiting) return { icon: '❚❚', kind: 'paused', text: `${name}: waiting for beam` }
      return { icon: '▶', kind: 'running', text: `${name}: running` }
    case 'paused':
      return { icon: '❚❚', kind: 'paused', text: `${name}: paused` }
    case 'finished':
      return { icon: '●', kind: 'succeeded', text: `${name}: finished` }
    case 'aborted':
      return { icon: '✕', kind: 'failed', text: `${name}: aborted` }
    case 'failed':
      return { icon: '✕', kind: 'failed', text: `${name}: failed` }
    default:
      return { icon: '○', kind: 'idle', text: 'No sequence has run since the backend started.' }
  }
}

type ExchangeLogEntry = { enabled: boolean; file: string | null; since: number | null; note: string | null }

/** Switches the log of the raw exchange with the control server on and off. */
function ExchangeLogSwitch({
  entry,
  onChange,
}: {
  entry: ExchangeLogEntry | undefined
  onChange: (enabled: boolean) => void
}) {
  const on = entry?.enabled === true
  return (
    <label
      className="switch"
      title={
        on
          ? `Every request and answer is written to data/exchange/${entry?.file ?? ''}`
          : 'Write every request to the control server and its answer to a file in data/exchange'
      }
    >
      <input type="checkbox" role="switch" checked={on} onChange={(e) => onChange(e.target.checked)} />
      Log the control server exchange
      {on && entry?.since ? <span className="hint"> · on since {formatWhen(entry.since)}</span> : null}
      {!on && entry?.note ? <span className="hint"> · {entry.note}</span> : null}
    </label>
  )
}

type LongWaitEntry = { enabled: boolean; max_wait_s: number; warn_after_s: number }

/** Switches the long wait for the control server on and off. Only the
 *  operator at the helm can change it. */
function LongWaitSwitch({
  entry,
  canChange,
  onChange,
}: {
  entry: LongWaitEntry | undefined
  canChange: boolean
  onChange: (enabled: boolean) => void
}) {
  const on = entry?.enabled === true
  const minutes = Math.round((entry?.max_wait_s ?? 3600) / 60)
  const warn = Math.round(entry?.warn_after_s ?? 60)
  return (
    <label
      className="switch"
      title={
        `For when the control server is stopped at a breakpoint: wait up to ${minutes} min for an answer ` +
        `instead of giving up after a few seconds. The operators get an e-mail after ${warn} s without an answer.` +
        (canChange ? '' : ' Take control to change it.')
      }
    >
      <input
        type="checkbox"
        role="switch"
        checked={on}
        disabled={!canChange}
        onChange={(e) => onChange(e.target.checked)}
      />
      Wait up to {minutes} min for the control server
    </label>
  )
}

function RunLog({ lines }: { lines: LogLine[] }) {
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    // keep the newest line in view
    if (box.current) box.current.scrollTop = box.current.scrollHeight
  }, [lines.length])
  if (lines.length === 0) return <p className="hint">Nothing logged yet.</p>
  return (
    <div className="run-log" ref={box} role="log" aria-label="Run log">
      {lines.map((line, index) => (
        <div key={index} className={line.level === 'error' ? 'log-error' : ''}>
          <span className="log-time">{formatClock(line.ts)}</span>
          {line.level === 'error' && <span aria-hidden="true">✕ </span>}
          {line.text}
        </div>
      ))}
    </div>
  )
}

export function Sequences({ me, state }: { me: Me; state: Record<string, unknown> }) {
  const defs = useSequenceDefs()
  const [selected, setSelected] = useState('')
  const [plan, setPlan] = useState<StepNode[] | null>(null)
  const [error, setError] = useState('')
  const helm = state['helm'] as HelmEntry | undefined
  const sequence = (state['sequence'] as SequenceEntry | undefined) ?? { state: 'idle' }
  // the step tree of the running or the last sequence
  const outline = (state['sequence_outline'] as { nodes: StepNode[] } | undefined)?.nodes ?? []
  const iHold = me.user !== null && helm?.holder === me.user
  const active = sequence.state === 'running' || sequence.state === 'paused'
  const status = statusLine(sequence)
  const chosen = defs.find((d) => d.name === selected) ?? defs[0]
  const exchange = state['exchange_log'] as ExchangeLogEntry | undefined

  const run = async (action: () => Promise<unknown>) => {
    setError('')
    try {
      await action()
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const showPlan = () =>
    run(async () => {
      setPlan(null)
      const body = (await call('GET', `/api/sequences/${chosen.name}/plan`)) as {
        outline?: StepNode[]
        steps: string[]
      }
      // a backend that has not been restarted yet sends the steps as plain text only
      const plain = (title: string, id: number): StepNode => ({
        id, kind: 'line', title, state: 'planned', started: null, finished: null, children: [],
      })
      setPlan(body.outline ?? body.steps.map(plain))
    })

  const start = () =>
    run(async () => {
      await post(`/api/sequences/${chosen.name}/start`)
      setPlan(null)
    })

  const exchangeSwitch = (
    <>
      <ExchangeLogSwitch
        entry={exchange}
        onChange={(enabled) => run(() => post('/api/exchange-log', { enabled }))}
      />
      <LongWaitSwitch
        entry={state['long_wait'] as LongWaitEntry | undefined}
        canChange={iHold}
        onChange={(enabled) => run(() => post('/api/long-wait', { enabled }))}
      />
    </>
  )

  return (
    <div className="columns overview">
    <section className="column" aria-label="Sequences">
      <h2>Sequences</h2>
      <div className="command-panel fill">
        <p className={`command-status phase-${status.kind}`} role="status">
          <span className="status-icon" aria-hidden="true">
            {status.icon}
          </span>
          <strong>{status.text}</strong>
          {sequence.user && (
            <>
              {' '}
              · started by {sequence.user}
              {sequence.started ? ` at ${formatWhen(sequence.started)}` : ''}
            </>
          )}
          {active && sequence.waiting && <span className="command-detail">{sequence.waiting}</span>}
          {active && !sequence.waiting && sequence.step && <span className="command-detail">Step: {sequence.step}</span>}
          {!active && sequence.detail && <span className="command-detail">{sequence.detail}</span>}
        </p>

        {active && iHold && (
          <div className="helm-actions">
            {sequence.state === 'running' && (
              <button
                type="button"
                className="warn"
                disabled={sequence.pause_requested || sequence.abort_requested}
                onClick={() => run(() => post('/api/sequence/pause'))}
              >
                Pause after this command
              </button>
            )}
            {sequence.state === 'paused' && (
              <button type="button" className="primary" onClick={() => run(() => post('/api/sequence/resume'))}>
                Resume
              </button>
            )}
            <button
              type="button"
              className="danger"
              disabled={sequence.abort_requested}
              onClick={() => run(() => post('/api/sequence/abort'))}
            >
              Abort after this command
            </button>
            {exchangeSwitch}
          </div>
        )}

        {!active && me.user !== null && chosen && (
          <div className="sequence-start">
            <label>
              Sequence
              <select
                value={chosen.name}
                onChange={(e) => {
                  setSelected(e.target.value)
                  setPlan(null)
                }}
              >
                {defs.map((d) => (
                  <option key={d.name} value={d.name}>
                    {d.label}
                  </option>
                ))}
              </select>
            </label>
            <button type="button" onClick={showPlan}>
              Create Sequence
            </button>
            {/* Red while it can be pressed: it moves the robot. */}
            <button
              type="button"
              className="danger"
              disabled={!iHold || plan === null}
              title={
                plan === null
                  ? 'Create the sequence first, to see its steps'
                  : iHold
                    ? `Start: ${chosen.label}`
                    : 'Take control to start it'
              }
              onClick={start}
            >
              Start Sequence
            </button>
            {exchangeSwitch}
            {plan !== null && !iHold && <span className="hint">Take control to start it.</span>}
            <p className="hint">{chosen.description}</p>
            {plan && <StepTree nodes={plan} label={`Steps planned: ${chosen.label}`} />}
          </div>
        )}
        {!active && me.user === null && <p className="hint">Log in as an operator to run sequences.</p>}

        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        {sequence.state !== 'idle' && (
          <StepTree nodes={outline} label={active ? 'Steps' : `Steps of the last run: ${sequence.label ?? ''}`} />
        )}
      </div>
    </section>
    <section className="column" aria-label="Log">
      <h2>Log</h2>
      <div className="command-panel fill">
        <RunLog lines={sequence.log ?? []} />
      </div>
    </section>
    </div>
  )
}
