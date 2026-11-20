import { useCallback, useEffect, useRef, useState } from 'react'
import type { CSSProperties, ReactNode } from 'react'
import { post } from './auth'
import type { Me } from './auth'

type CameraEntry = {
  configured: boolean
  connected: boolean
  detail: string | null
  /** the camera's preset positions; null if it could not be asked */
  presets: string[] | null
  ptz_detail: string | null
}

const RETRY_MS = 3000
// While a button is held the page asks again this often; the backend stops
// the camera by itself when the asking ends (about a second later).
const HOLD_MS = 400
// Speed of turning and zooming, of 100.
const SPEED = 35

/**
 * The live picture. The backend passes the pictures on as one endless MJPEG
 * stream, which an <img> shows as it comes. The frame gets the shape of the
 * picture; `style` gives it its height or its width.
 */
function Picture({
  state,
  link,
  style,
  onPick,
}: {
  state: Record<string, unknown>
  link: string
  style: (ratio: number) => CSSProperties
  /** a click on the picture: where, 0 to 1 from the left and from the top */
  onPick?: (x: number, y: number) => void
}) {
  const camera = state['camera'] as CameraEntry | undefined
  // A new number makes the browser ask for the stream again.
  const [attempt, setAttempt] = useState(0)
  const [broken, setBroken] = useState(false)
  // Width over height of the picture, known once the first one has come.
  const [ratio, setRatio] = useState(16 / 9)

  useEffect(() => {
    if (!broken) return
    const timer = setTimeout(() => {
      setBroken(false)
      setAttempt((n) => n + 1)
    }, RETRY_MS)
    return () => clearTimeout(timer)
  }, [broken])

  const configured = camera?.configured === true
  let note: string | null = null
  if (!configured) note = camera ? 'No camera is configured.' : 'Waiting for the backend…'
  else if (link !== 'open') note = 'No connection to the backend.'
  else if (!camera.connected) note = camera.detail ? `No picture: ${camera.detail}.` : 'Connecting to the camera…'

  return (
    <div className="camera-view" style={{ ...style(ratio), aspectRatio: String(ratio) }}>
      {/* Only while the link to the backend is open: the stream ends without
          an error when the backend restarts, and is asked for again when
          the link comes back. */}
      {configured && !broken && link === 'open' && (
        <img
          key={attempt}
          className={onPick ? 'camera-pick' : undefined}
          src={`/api/camera/stream?n=${attempt}`}
          alt="Live picture of the camera"
          title={onPick ? 'Click a point to bring it to the middle of the picture' : undefined}
          onError={() => setBroken(true)}
          onLoad={(event) => {
            const { naturalWidth, naturalHeight } = event.currentTarget
            if (naturalWidth > 0 && naturalHeight > 0) setRatio(naturalWidth / naturalHeight)
          }}
          onClick={(event) => {
            if (!onPick) return
            const box = event.currentTarget.getBoundingClientRect()
            if (box.width === 0 || box.height === 0) return
            onPick((event.clientX - box.left) / box.width, (event.clientY - box.top) / box.height)
          }}
        />
      )}
      {note && (
        <div className="camera-note" role="status">
          {note}
        </div>
      )}
    </div>
  )
}

/** The picture in a box like a plot, for the top bar. */
export function Camera({
  state,
  link,
  height,
}: {
  state: Record<string, unknown>
  link: string
  height: number
}) {
  return (
    <div className="plot-tile" role="group" aria-label="Camera">
      <div className="plot-title">Camera</div>
      <Picture state={state} link={link} style={() => ({ height, maxWidth: '100%' })} />
    </div>
  )
}

/** A button that acts for as long as it is held, with the mouse, a finger or
 *  the space bar. */
function HoldButton({
  label,
  disabled,
  begin,
  end,
  children,
}: {
  label: string
  disabled: boolean
  begin: () => void
  end: () => void
  children: ReactNode
}) {
  const held = useRef(false)
  const press = () => {
    if (held.current || disabled) return
    held.current = true
    begin()
  }
  const release = () => {
    if (!held.current) return
    held.current = false
    end()
  }
  return (
    <button
      type="button"
      className="ptz-button"
      aria-label={label}
      title={`${label} (hold)`}
      disabled={disabled}
      onPointerDown={(event) => {
        event.currentTarget.setPointerCapture(event.pointerId)
        press()
      }}
      onPointerUp={release}
      onPointerCancel={release}
      onLostPointerCapture={release}
      onBlur={release}
      onKeyDown={(event) => {
        if ((event.key === ' ' || event.key === 'Enter') && !event.repeat) press()
      }}
      onKeyUp={(event) => {
        if (event.key === ' ' || event.key === 'Enter') release()
      }}
      onContextMenu={(event) => event.preventDefault()}
    >
      {children}
    </button>
  )
}

/** The camera page: the picture large, and beside it the buttons to turn and zoom it. */
export function CameraPage({ me, state, link }: { me: Me; state: Record<string, unknown>; link: string }) {
  const camera = state['camera'] as CameraEntry | undefined
  const [error, setError] = useState('')
  const timer = useRef<ReturnType<typeof setInterval> | null>(null)

  const send = useCallback((body: Record<string, unknown>) => {
    post('/api/camera/ptz', body).then(
      () => setError(''),
      (e: Error) => setError(e.message),
    )
  }, [])

  const release = useCallback(() => {
    if (timer.current === null) return
    clearInterval(timer.current)
    timer.current = null
    send({ action: 'stop' })
  }, [send])

  const hold = (body: Record<string, unknown>) => {
    if (timer.current !== null) clearInterval(timer.current)
    send(body)
    timer.current = setInterval(() => send(body), HOLD_MS)
  }

  // Leaving the page while a button is held: stop the camera.
  useEffect(() => release, [release])

  const configured = camera?.configured === true
  let why = ''
  if (!configured) why = 'No camera is configured.'
  else if (me.user === null) why = 'Log in as an operator to turn and zoom the camera.'
  else if (link !== 'open') why = 'No connection to the backend.'
  else if (camera.ptz_detail) why = `The camera cannot be turned: ${camera.ptz_detail}.`
  const off = why !== ''

  const button = (label: string, icon: string, body: Record<string, unknown>) => (
    <HoldButton label={label} disabled={off} begin={() => hold(body)} end={release}>
      <span aria-hidden="true">{icon}</span>
    </HoldButton>
  )

  return (
    <section className="camera-page">
      <h2>Camera</h2>
      <div className="camera-layout">
        <Picture
          state={state}
          link={link}
          // As large as fits beside the buttons, but no taller than the window allows.
          style={(ratio) => ({ width: `min(100%, calc(70vh * ${ratio}))` })}
          onPick={off ? undefined : (x, y) => send({ action: 'center', x, y })}
        />

        <div className="ptz">
          <div className="ptz-pad" role="group" aria-label="Turn and zoom the camera">
            {button('Turn up', '▲', { action: 'move', pan: 0, tilt: SPEED })}
            {button('Turn down', '▼', { action: 'move', pan: 0, tilt: -SPEED })}
            {button('Turn left', '◀', { action: 'move', pan: -SPEED, tilt: 0 })}
            {button('Turn right', '▶', { action: 'move', pan: SPEED, tilt: 0 })}
            {button('Zoom in', '+', { action: 'zoom', speed: SPEED })}
            {button('Zoom out', '−', { action: 'zoom', speed: -SPEED })}
          </div>
          {why && (
            <p className="hint" role="status">
              {why}
            </p>
          )}
          {error && (
            <p className="ptz-error" role="alert">
              {error}
            </p>
          )}
        </div>
      </div>
    </section>
  )
}
