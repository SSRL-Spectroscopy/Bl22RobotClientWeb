import { useEffect, useState } from 'react'

type CameraEntry = { configured: boolean; connected: boolean; detail: string | null }

const RETRY_MS = 3000

/** The live camera picture, in a box like a plot. The backend passes the
 *  pictures on as one endless MJPEG stream, which an <img> shows as it comes. */
export function Camera({
  state,
  link,
  height,
}: {
  state: Record<string, unknown>
  link: string
  height: number
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
    <div className="plot-tile" role="group" aria-label="Camera">
      <div className="plot-title">Camera</div>
      <div className="camera-view" style={{ height, aspectRatio: String(ratio) }}>
        {/* Only while the link to the backend is open: the stream ends without
            an error when the backend restarts, and is asked for again when
            the link comes back. */}
        {configured && !broken && link === 'open' && (
          <img
            key={attempt}
            src={`/api/camera/stream?n=${attempt}`}
            alt="Live picture of the camera"
            onError={() => setBroken(true)}
            onLoad={(event) => {
              const { naturalWidth, naturalHeight } = event.currentTarget
              if (naturalWidth > 0 && naturalHeight > 0) setRatio(naturalWidth / naturalHeight)
            }}
          />
        )}
        {note && (
          <div className="camera-note" role="status">
            {note}
          </div>
        )}
      </div>
    </div>
  )
}
