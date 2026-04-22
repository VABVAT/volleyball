import { useMemo } from 'react'
import { useCurrentMetrics } from '../../hooks/useCurrentMetrics'
import { useResults } from '../../hooks/useResults'
import { useTimeSeries } from '../../hooks/useTimeSeries'
import { SimpleLineChart, type SimplePoint } from '../../components/simple/SimpleLineChart'
import { SimpleResultsTable } from '../../components/simple/SimpleResultsTable'
import { SimpleStatCard } from '../../components/simple/SimpleStatCard'
import type { RawSnapshot } from '../../api/types'

function sumRawEventLag(snapshot: RawSnapshot | null): number {
  if (!snapshot) return 0
  const raw = snapshot.sp.raw
  let sum = 0
  for (let p = 0; p < 50; p++) {
    const k1 = `stream_processor_consumer_lag_messages{partition="${p}",topic="raw-events"}`
    const k2 = `stream_processor_consumer_lag_messages{topic="raw-events",partition="${p}"}`
    const v = raw[k1] ?? raw[k2]
    if (v == null) {
      if (p >= 3) break
      continue
    }
    sum += v
  }
  return sum
}

function enrichedTotal(snapshot: RawSnapshot | null): number {
  return snapshot?.sp.raw['stream_processor_enriched_events_total'] ?? 0
}

function pointsEnrichedEps(series: RawSnapshot[]): SimplePoint[] {
  return series.map((s, i) => {
    const prev = series[i - 1]
    if (!prev || !s.sp.reachable || !prev.sp.reachable) return { t: s.ts, v: null }
    const dt = s.ts - prev.ts
    if (dt <= 0) return { t: s.ts, v: null }
    const cur = s.sp.raw['stream_processor_enriched_events_total'] ?? 0
    const p = prev.sp.raw['stream_processor_enriched_events_total'] ?? 0
    return { t: s.ts, v: Math.max(0, (cur - p) / dt) }
  })
}

function pointsLag(series: RawSnapshot[]): SimplePoint[] {
  return series.map((s) => ({ t: s.ts, v: sumRawEventLag(s) }))
}

export function SimpleDashboard() {
  const { data } = useCurrentMetrics(2000)
  const series = useTimeSeries(600, 3000)
  const { rows } = useResults(5000)

  const current = data?.snapshot ?? null
  const derived = data?.derived ?? null
  const enriched = enrichedTotal(current)

  const enrichedEpsNow = useMemo(() => {
    if (series.length < 2) return null
    const last = series[series.length - 1]
    const prev = series[series.length - 2]
    if (!last.sp.reachable || !prev.sp.reachable) return null
    const dt = last.ts - prev.ts
    if (dt <= 0) return null
    const cur = last.sp.raw['stream_processor_enriched_events_total'] ?? 0
    const p = prev.sp.raw['stream_processor_enriched_events_total'] ?? 0
    return Math.max(0, (cur - p) / dt)
  }, [series])

  const epsPoints = useMemo(() => pointsEnrichedEps(series), [series])
  const lagPoints = useMemo(() => pointsLag(series), [series])
  const latest10 = rows.slice(0, 10)

  const fmt = (v: number | null, digits = 1) => (v == null ? '—' : v.toFixed(digits))

  return (
    <div className="space-y-4">
      <div className="border border-black bg-white px-3 py-2">
        <div className="text-sm font-bold text-black">Pipeline Dashboard (simple)</div>
        <div className="text-xs text-black">
          Focus: enriched output + raw-events lag. Refresh page if you just started the stack.
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <SimpleStatCard label="Enriched total" value={enriched.toFixed(0)} />
        <SimpleStatCard label="Enriched / sec (recent)" value={fmt(enrichedEpsNow, 1)} />
        <SimpleStatCard label="Enriched %" value={fmt(derived?.success_rate_pct ?? null, 1)} />
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <SimpleLineChart title="Enriched / sec" points={epsPoints} unit="" />
        <SimpleLineChart title="Raw-events lag (sum)" points={lagPoints} unit="" />
      </div>

      <SimpleResultsTable rows={latest10} />
    </div>
  )
}

