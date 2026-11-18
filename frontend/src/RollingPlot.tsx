import { useEffect, useRef, useState } from 'react'
import uPlot from 'uplot'
import 'uplot/dist/uPlot.min.css'
import { live } from './live'
import type { PvDef } from './pvs'

const REDRAW_MS = 250

function cssVar(element: Element, name: string): string {
  return getComputedStyle(element).getPropertyValue(name).trim()
}

type Props = {
  title: string
  pvs: PvDef[]
  windowMin: number
  height: number
  /** CSS variables with the series colours, by position. Light and dark mode each have their own step. */
  colors: string[]
  /** decimals in the legend values */
  digits: number
}

/** A rolling plot of one or more values against time. */
export function RollingPlot({ title, pvs, windowMin, height, colors, digits }: Props) {
  const holder = useRef<HTMLDivElement>(null)
  const windowRef = useRef(windowMin)
  // Rebuild the plot when the colour scheme changes, to pick up the new colours.
  const [scheme, setScheme] = useState(0)

  useEffect(() => {
    windowRef.current = windowMin
  }, [windowMin])

  useEffect(() => {
    const query = window.matchMedia('(prefers-color-scheme: dark)')
    const onChange = () => setScheme((n) => n + 1)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  useEffect(() => {
    const element = holder.current
    if (!element || pvs.length === 0) return

    const axisInk = cssVar(element, '--text-muted')
    const grid = cssVar(element, '--grid')
    const unit = pvs[0].unit
    const axis: uPlot.Axis = {
      stroke: axisInk,
      grid: { stroke: grid, width: 1 },
      ticks: { stroke: grid, width: 1 },
    }

    const plot = new uPlot(
      {
        width: element.clientWidth,
        height,
        scales: { x: { time: true, auto: false } },
        axes: [axis, { ...axis, size: 62, label: unit, labelSize: 18 }],
        series: [
          { label: 'Time' },
          ...pvs.map((pv, i) => ({
            label: pv.label,
            stroke: cssVar(element, colors[i % colors.length]),
            width: 2,
            spanGaps: true,
            points: { show: false },
            value: (_: uPlot, v: number | null) => (v == null ? '–' : `${v.toFixed(digits)} ${unit}`),
          })),
        ],
        cursor: { drag: { x: false, y: false } },
        legend: { live: true },
      },
      [[], ...pvs.map(() => [])],
      element,
    )

    const redraw = () => {
      const now = live.serverNow()
      const from = now - windowRef.current * 60
      // Each value has its own timestamps; join them onto one time axis.
      const tables = pvs.map((pv) => {
        const series = live.history.get(pv.key) ?? { t: [], v: [] }
        let start = 0
        while (start < series.t.length && series.t[start] < from) start++
        // keep one point before the window so the line reaches the left edge
        start = Math.max(0, start - 1)
        return [series.t.slice(start), series.v.slice(start)] as uPlot.AlignedData
      })
      const filled = tables.filter((table) => table[0].length > 0)
      if (filled.length === pvs.length) {
        plot.setData(uPlot.join(tables), false)
      } else if (filled.length === 0) {
        plot.setData([[], ...pvs.map(() => [])], false)
      }
      plot.setScale('x', { min: from, max: now })
    }

    redraw()
    const timer = setInterval(redraw, REDRAW_MS)
    const resize = new ResizeObserver(() => {
      plot.setSize({ width: element.clientWidth, height })
    })
    resize.observe(element)

    return () => {
      clearInterval(timer)
      resize.disconnect()
      plot.destroy()
    }
  }, [pvs, scheme, height, colors, digits])

  return (
    <div className="plot-tile" role="group" aria-label={title}>
      <div className="plot-title">{title}</div>
      <div className="plot" ref={holder} />
    </div>
  )
}
