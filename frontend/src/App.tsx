import { useEffect, useMemo, useState } from 'react'
import { useAuth } from './auth'
import { Commands } from './Commands'
import { Helm } from './Helm'
import { useLive } from './live'
import type { ControlEntry, HelmEntry } from './live'
import { Login } from './Login'
import { usePvDefs } from './pvs'
import { Readouts } from './Readouts'
import { RollingPlot } from './RollingPlot'
import { Samples } from './Samples'
import { Sequences } from './Sequences'
import { Subsystems } from './Subsystems'

function App() {
  const { link, state } = useLive()
  const { me, login, logout, refresh } = useAuth()
  const pvs = usePvDefs()
  const plotted = useMemo(() => pvs.filter((pv) => pv.plot), [pvs])
  const control = state['control'] as ControlEntry | undefined

  const [simulation, setSimulation] = useState(false)

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

  return (
    <main>
      <header>
        <h1>BL22 Robot Client</h1>
        <div className="links">
          <span className={link === 'open' ? 'pill ok' : 'pill down'}>
            <span aria-hidden="true">{link === 'open' ? '●' : '○'} </span>
            {link === 'open' ? 'Live' : link === 'connecting' ? 'Connecting…' : 'Reconnecting…'}
          </span>
          <span className={control?.connected ? 'pill ok' : 'pill down'} title={control?.detail ?? ''}>
            <span aria-hidden="true">{control?.connected ? '●' : '○'} </span>
            Control server {control?.connected ? 'connected' : 'not connected'}
          </span>
          <Login me={me} login={login} logout={logout} />
        </div>
      </header>

      {simulation && (
        <p className="banner simulation" role="status">
          <strong>Simulation.</strong> The control server is simulated: nothing on the beamline moves.
        </p>
      )}

      {link !== 'open' && (
        <p className="banner" role="status">
          No connection to the backend. The values below are the last ones received and may be out of date.
        </p>
      )}

      <Helm me={me} helm={state['helm'] as HelmEntry | undefined} />
      <Subsystems state={state} />
      <Sequences me={me} state={state} />
      <Samples me={me} state={state} />
      <Commands me={me} state={state} />
      <RollingPlot pvs={plotted} />
      <Readouts pvs={pvs} state={state} />
    </main>
  )
}

export default App
