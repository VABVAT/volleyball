import { useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
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
  const duplicates = current?.sp.raw['stream_processor_duplicate_events_total'] ?? 0
  const retryOut = current?.sp.raw['stream_processor_retry_published_total'] ?? 0
  const errorsTotal = useMemo(() => {
    const raw = current?.sp.raw ?? {}
    return Object.entries(raw)
      .filter(([k]) => k.startsWith('stream_processor_errors_total'))
      .reduce((acc, [, v]) => acc + (typeof v === 'number' ? v : 0), 0)
  }, [current])

  const [eps, setEps] = useState<number>(20)
  const [everyN, setEveryN] = useState<number>(12)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string>('')

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const c = await api.producerControls()
        if (cancelled) return
        setEps(Math.round(c.speed.events_per_sec))
        setEveryN(c.duplicates.duplicate_every_n)
      } catch {
        /* ignore */
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

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

  const run = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label)
    setMsg(`${label}...`)
    try {
      await fn()
      setMsg(`${label}: OK`)
    } catch (e) {
      setMsg(`${label}: ${String(e)}`)
    } finally {
      setBusy(null)
      setTimeout(() => setMsg(''), 2500)
    }
  }

  return (
    <div className="space-y-4">
      <div className="border border-black bg-white px-3 py-2">
        <div className="text-sm font-bold text-black">Pipeline Dashboard (simple)</div>
        <div className="text-xs text-black">
          Focus: enriched output + raw-events lag. Refresh page if you just started the stack.
        </div>
      </div>

      <div className="border border-black bg-white p-3">
        <div className="text-sm font-semibold text-black">Controls</div>
        <div className="mt-2 grid grid-cols-1 gap-3 md:grid-cols-3">
          <div className="border border-black p-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-black">Stream speed</div>
            <div className="mt-2 flex items-center gap-3">
              <input
                type="range"
                min={1}
                max={200}
                value={eps}
                onChange={(e) => setEps(Number(e.target.value))}
                className="w-full"
              />
              <div className="w-16 text-right font-mono text-sm text-black">{eps} eps</div>
            </div>
            <button
              disabled={busy !== null}
              className="mt-2 border border-black bg-white px-2 py-1 text-sm font-semibold text-black disabled:opacity-50"
              onClick={() =>
                run('Set speed', async () => {
                  await api.setProducerSpeed(eps)
                })
              }
            >
              Apply
            </button>
          </div>

          <div className="border border-black p-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-black">Duplicates</div>
            <div className="mt-2 flex items-center gap-2">
              <span className="text-sm text-black">Every</span>
              <input
                type="number"
                min={1}
                value={everyN}
                onChange={(e) => setEveryN(Number(e.target.value))}
                className="w-24 border border-black px-2 py-1 font-mono text-sm text-black"
              />
              <span className="text-sm text-black">events</span>
            </div>
            <button
              disabled={busy !== null}
              className="mt-2 border border-black bg-white px-2 py-1 text-sm font-semibold text-black disabled:opacity-50"
              onClick={() =>
                run('Set duplicates', async () => {
                  await api.setProducerDuplicates(everyN)
                })
              }
            >
              Apply
            </button>
          </div>

          <div className="border border-black p-3">
            <div className="text-xs font-semibold uppercase tracking-wide text-black">Generate</div>
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                disabled={busy !== null}
                className="border border-black bg-white px-2 py-1 text-sm font-semibold text-black disabled:opacity-50"
                onClick={() => run('Simulate down', () => api.simulateDown())}
              >
                Simulate user down
              </button>
              <button
                disabled={busy !== null}
                className="border border-black bg-white px-2 py-1 text-sm font-semibold text-black disabled:opacity-50"
                onClick={() => run('Restore user', () => api.restoreUser())}
              >
                Restore user
              </button>
            </div>
            {msg && <div className="mt-2 font-mono text-xs text-black">{msg}</div>}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <SimpleStatCard label="Enriched total" value={enriched.toFixed(0)} />
        <SimpleStatCard label="Enriched / sec (recent)" value={fmt(enrichedEpsNow, 1)} />
        <SimpleStatCard label="Enriched %" value={fmt(derived?.success_rate_pct ?? null, 1)} />
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <SimpleStatCard label="Duplicates total" value={duplicates.toFixed(0)} />
        <SimpleStatCard label="Retry out total" value={retryOut.toFixed(0)} />
        <SimpleStatCard label="Errors total" value={errorsTotal.toFixed(0)} />
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <SimpleLineChart title="Enriched / sec" points={epsPoints} unit="" />
        <SimpleLineChart title="Raw-events lag (sum)" points={lagPoints} unit="" />
      </div>

      <SimpleResultsTable rows={latest10} />
    </div>
  )
}

