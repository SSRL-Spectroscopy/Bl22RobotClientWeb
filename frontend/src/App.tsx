import { useEffect, useMemo, useState } from 'react'
import { useAuth } from './auth'
import { Camera, CameraPage } from './Camera'
import { ManualControl, SampleHandling } from './Commands'
import { Crystal } from './Crystal'
import { HelmButton, HelmNotice } from './Helm'
import { formatWhen } from './format'
import { useLive } from './live'
import type { ControlEntry, HelmEntry } from './live'
import { Login } from './Login'
import { usePvDefs } from './pvs'
import type { PvDef } from './pvs'
import { usePlotWindow, WINDOWS_MIN } from './plotWindow'
import { RollingPlot } from './RollingPlot'
import { Samples } from './Samples'
import { Sequences } from './Sequences'
import { Signals } from './Signals'
import { Subsystems } from './Subsystems'

const TABS = [
  { id: 'overview', label: 'Overview' },
  { id: 'queue', label: 'Queue' },
  { id: 'signals', label: 'Signals' },
  { id: 'camera', label: 'Camera' },
  { id: 'manual', label: 'Manual control' },
  { id: 'settings', label: 'Settings' },
] as const
type TabId = (typeof TABS)[number]['id']
const TAB_KEY = 'tab'

// Series colours by position, from CSS, so light and dark mode each get their
// own validated step. The mono plot has its own colour: it is another quantity.
const ADC_COLORS = ['--series-1', '--series-2', '--series-3', '--series-4']
const MONO_COLORS = ['--series-mono']
const PLOT_HEIGHT = 300

function readStoredTab(): TabId {
  try {
    const stored = localStorage.getItem(TAB_KEY)
    if (TABS.some((tab) => tab.id === stored)) return stored as TabId
  } catch {
    // storage unavailable
  }
  return 'overview'
}

type PvEntry = { connected: boolean; value: unknown }
type BeamEntry = { low_ma?: number }

// Below the first the measurement sequence waits (the backend says where that
// is; this is its default). Below the second there is no beam at all.
const BEAM_LOW_MA = 400
const BEAM_NONE_MA = 10

type Tone = { tone: 'warn' | 'alarm'; note?: string }

function beamTone(value: unknown, lowMa: number): Tone | undefined {
  if (typeof value !== 'number') return undefined
  if (value < BEAM_NONE_MA) return { tone: 'alarm', note: 'No beam' }
  if (value < lowMa) return { tone: 'warn' }
  return undefined
}

/** One value: a number with its unit, or a state in words. */
function Tile({
  pv,
  state,
  digits = 0,
  tone,
  label,
  wrap = false,
}: {
  pv: PvDef | undefined
  state: Record<string, unknown>
  digits?: number
  tone?: (value: unknown) => Tone | undefined
  /** instead of the label of the PV */
  label?: string
  /** a message: may run onto a second line */
  wrap?: boolean
}) {
  if (!pv) return null
  const entry = state[pv.key] as PvEntry | undefined
  const value = entry?.value
  const known = typeof value === 'number' || typeof value === 'string'
  const stale = known && entry?.connected !== true
  const alert = known && !stale ? tone?.(value) : undefined
  let className = 'tile'
  if (stale || !known) className += ' tile-stale'
  if (alert) className += ` tile-${alert.tone}`
  return (
    <div className={className} title={stale ? 'Not connected: last value' : ''}>
      <div className="tile-label">{label ?? pv.label}</div>
      <div className={wrap ? 'tile-value tile-wrap' : 'tile-value'} title={wrap && typeof value === 'string' ? value : undefined}>
        {typeof value === 'number' ? value.toFixed(digits) : typeof value === 'string' ? value : '–'}
        {pv.unit && <span className="tile-unit"> {pv.unit}</span>}
      </div>
      {alert?.note && (
        <div className="tile-note" role="status">
          <span aria-hidden="true">⚠ </span>
          {alert.note}
        </div>
      )}
    </div>
  )
}

function App() {
  const { link, state } = useLive()
  const { me, login, logout, refresh } = useAuth()
  const pvs = usePvDefs()
  const adc = useMemo(() => pvs.filter((pv) => pv.plot && pv.group === 'adc'), [pvs])
  const mono = useMemo(() => pvs.filter((pv) => pv.plot && pv.group === 'mono'), [pvs])
  const control = state['control'] as ControlEntry | undefined
  const longWait = state['long_wait'] as
    | { max_wait_s: number; waiting: { command: string; since: number } | null }
    | undefined
  const helm = state['helm'] as HelmEntry | undefined
  const beam = state['beam'] as BeamEntry | undefined
  const iHold = me.user !== null && helm?.holder === me.user

  const [simulation, setSimulation] = useState(false)
  const [tab, setTab] = useState<TabId>(readStoredTab)
  const [windowMin, setWindowMin] = usePlotWindow()

  // After the link comes back (for example after a backend restart, which
  // ends every session), check whether this browser is still logged in and
  // whether this backend is the simulator.
  useEffect(() => {
    if (link !== 'open') return
    refresh()
    fetch('/api/health')
      .then((response) => response.json())
      .then((body) => setSimulation(body.simulation === true))
      .catch(() => undefined)
  }, [link, refresh])

  const choose = (id: TabId) => {
    setTab(id)
    try {
      localStorage.setItem(TAB_KEY, id)
    } catch {
      // not being able to remember the tab is harmless
    }
  }

  return (
    // The page colour says whether this window has control: amber with, pale blue without.
    <div className={iHold ? 'app has-control' : 'app no-control'}>
      <header className="topbar">
        <div className="topbar-row">
          <h1>BL22 Robot Client</h1>
          <span className={link === 'open' ? 'pill ok' : 'pill down'}>
            <span aria-hidden="true">{link === 'open' ? '●' : '○'} </span>
            {link === 'open' ? 'Live' : link === 'connecting' ? 'Connecting…' : 'Reconnecting…'}
          </span>
          <span className={control?.connected ? 'pill ok' : 'pill down'} title={control?.detail ?? ''}>
            <span aria-hidden="true">{control?.connected ? '●' : '○'} </span>
            Control server {control?.connected ? 'connected' : 'not connected'}
          </span>
          <Subsystems state={state} />
          <span className="topbar-space" />
          <HelmButton me={me} helm={helm} />
          <Login me={me} login={login} logout={logout} />
        </div>

        {simulation && (
          <p className="strip strip-simulation" role="status">
            <strong>Simulation.</strong> The control server is simulated: nothing on the beamline moves.
          </p>
        )}
        {longWait?.waiting && (
          <p className="strip strip-waiting" role="alert">
            <strong>The control server is not answering.</strong> No answer to "{longWait.waiting.command}" since{' '}
            {formatWhen(longWait.waiting.since)}. The backend waits up to {Math.round(longWait.max_wait_s / 60)} min. If
            the control server is stopped at a breakpoint, let it go on.
          </p>
        )}
        {link !== 'open' && (
          <p className="strip strip-decide" role="status">
            No connection to the backend. The values shown are the last ones received and may be out of date.
          </p>
        )}
        <HelmNotice me={me} helm={helm} />

        <div className="topbar-plots">
          <RollingPlot
            title="Ion chambers and PIPS"
            pvs={adc}
            windowMin={windowMin}
            height={PLOT_HEIGHT}
            colors={ADC_COLORS}
            digits={5}
          />
          <RollingPlot
            title="Monochromator"
            pvs={mono}
            windowMin={windowMin}
            height={PLOT_HEIGHT}
            colors={MONO_COLORS}
            digits={1}
            minRange={0.1}
          />
          <Camera state={state} link={link} height={PLOT_HEIGHT} />
          <div className="tiles">
            <Tile
              pv={pvs.find((pv) => pv.name === 'SPEAR:BeamCurrAvg')}
              state={state}
              digits={1}
              tone={(value) => beamTone(value, beam?.low_ma ?? BEAM_LOW_MA)}
            />
            <Tile pv={pvs.find((pv) => pv.name === 'BL22:SCAN:MASTER.RTME')} state={state} />
            <Tile pv={pvs.find((pv) => pv.name === 'BL22:SCAN:MASTER.TRIG')} state={state} />
            <Tile
              pv={pvs.find((pv) => pv.name === 'XSP3_7Chan:det1:StatusMessage_RBV')}
              state={state}
              label="XSPRESS status"
              wrap
            />
            <div className="segmented" role="group" aria-label="Time window of the plots">
              {WINDOWS_MIN.map((minutes) => (
                <button
                  key={minutes}
                  type="button"
                  aria-pressed={minutes === windowMin}
                  onClick={() => setWindowMin(minutes)}
                >
                  {minutes} min
                </button>
              ))}
            </div>
          </div>
        </div>
      </header>

      <div className="body">
        <nav className="sidebar" aria-label="Pages">
          {TABS.map((item) => (
            <button
              key={item.id}
              type="button"
              aria-current={tab === item.id ? 'page' : undefined}
              onClick={() => choose(item.id)}
            >
              {item.label}
            </button>
          ))}
        </nav>
        <main>
          {tab === 'overview' && <Sequences me={me} state={state} />}
          {tab === 'queue' && <Samples me={me} state={state} />}
          {tab === 'signals' && <Signals pvs={pvs} state={state} />}
          {tab === 'camera' && <CameraPage me={me} state={state} link={link} />}
          {tab === 'manual' && <ManualControl me={me} state={state} />}
          {tab === 'settings' && (
            <>
              <Crystal me={me} state={state} />
              <SampleHandling me={me} state={state} />
            </>
          )}
        </main>
      </div>
    </div>
  )
}

export default App
