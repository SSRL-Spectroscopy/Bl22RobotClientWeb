import { useEffect, useState } from 'react'
import { post } from './auth'
import type { Me } from './auth'
import { live } from './live'
import type { HelmEntry } from './live'

/** Whole seconds until a deadline given in server time; ticks once a second. */
function useCountdown(deadline: number | null): number {
  const [, setTick] = useState(0)
  useEffect(() => {
    if (deadline === null) return
    const timer = setInterval(() => setTick((n) => n + 1), 500)
    return () => clearInterval(timer)
  }, [deadline])
  return deadline === null ? 0 : Math.max(0, Math.ceil(deadline - live.serverNow()))
}

export function Helm({ me, helm }: { me: Me; helm: HelmEntry | undefined }) {
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const holder = helm?.holder ?? null
  const request = helm?.request ?? null
  const seconds = useCountdown(request?.deadline ?? null)

  const act = async (path: string, body?: unknown) => {
    setBusy(true)
    setError('')
    try {
      await post(path, body)
    } catch (e) {
      setError((e as Error).message)
    } finally {
      setBusy(false)
    }
  }

  const iHold = me.user !== null && holder === me.user
  const iWait = me.user !== null && request?.user === me.user
  const decide = iHold && request !== null

  let status: string
  if (holder === null) status = 'Nobody has control.'
  else if (iHold) status = 'You have control.'
  else status = `${holder} has control.`

  return (
    <section className={decide ? 'helm helm-decide' : 'helm'} aria-label="Control">
      <div className="helm-row">
        <div>
          <h2>Control</h2>
          <p className="helm-status">{status}</p>
        </div>
        <div className="helm-actions">
          {me.user === null && <span className="hint">Log in as an operator to take control.</span>}
          {me.user !== null && !iHold && !iWait && (
            <button
              type="button"
              className="primary"
              disabled={busy || request !== null}
              onClick={() => act('/api/helm/request')}
            >
              {holder === null ? 'Take control' : 'Request control'}
            </button>
          )}
          {iWait && (
            <button type="button" disabled={busy} onClick={() => act('/api/helm/cancel')}>
              Cancel request
            </button>
          )}
          {iHold && !decide && (
            <button type="button" disabled={busy} onClick={() => act('/api/helm/release')}>
              Release control
            </button>
          )}
        </div>
      </div>

      {request && decide && (
        <div className="helm-request" role="alert">
          <p>
            <strong>{request.user}</strong> requests control. It transfers automatically in{' '}
            <strong>{seconds} s</strong> unless you answer.
          </p>
          <div className="helm-actions">
            <button
              type="button"
              className="primary"
              disabled={busy}
              onClick={() => act('/api/helm/respond', { accept: true })}
            >
              Transfer control
            </button>
            <button type="button" disabled={busy} onClick={() => act('/api/helm/respond', { accept: false })}>
              Deny
            </button>
          </div>
        </div>
      )}
      {request && !decide && (
        <p className="helm-request" role="status">
          {iWait ? 'You have' : <><strong>{request.user}</strong> has</>} asked {holder} for control. Without an
          answer it transfers automatically in <strong>{seconds} s</strong>.
        </p>
      )}
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
    </section>
  )
}
