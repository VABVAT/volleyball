import { useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import { useCurrentMetrics } from '../../hooks/useCurrentMetrics'
import { useTimeSeries } from '../../hooks/useTimeSeries'
import { SimpleLineChart, type SimplePoint } from '../../components/simple/SimpleLineChart'
import { SimpleStatCard } from '../../components/simple/SimpleStatCard'
import type { RawSnapshot } from '../../api/types'

/** Producer treats very large N as effectively no duplicates. */
const DUPLICATE_OFF_EVERY_N = 1_000_000

function enrichedTotal(snapshot: RawSnapshot | null): number {
  return snapshot?.sp.raw['stream_processor_enriched_events_total'] ?? 0
}

function sumStreamProcessorErrors(raw: Record<string, number>): number {
  return Object.entries(raw)
    .filter(([k]) => k.startsWith('stream_processor_errors_total'))
    .reduce((acc, [, v]) => acc + (typeof v === 'number' ? v : 0), 0)
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

/** Rate of raw-events intake (counter is consumed messages; ≈ producer rate when lag is stable). */
function pointsRawEventsEps(series: RawSnapshot[]): SimplePoint[] {
  const key = 'stream_processor_events_consumed_total'
  return series.map((s, i) => {
    const prev = series[i - 1]
    if (!prev || !s.sp.reachable || !prev.sp.reachable) return { t: s.ts, v: null }
    const dt = s.ts - prev.ts
    if (dt <= 0) return { t: s.ts, v: null }
    const cur = s.sp.raw[key] ?? 0
    const p = prev.sp.raw[key] ?? 0
    return { t: s.ts, v: Math.max(0, (cur - p) / dt) }
  })
}

function pointsCounterDerivativeEps(
  series: RawSnapshot[],
  getTotal: (raw: Record<string, number>) => number,
): SimplePoint[] {
  return series.map((s, i) => {
    const prev = series[i - 1]
    if (!prev || !s.sp.reachable || !prev.sp.reachable) return { t: s.ts, v: null }
    const dt = s.ts - prev.ts
    if (dt <= 0) return { t: s.ts, v: null }
    const cur = getTotal(s.sp.raw)
    const p = getTotal(prev.sp.raw)
    return { t: s.ts, v: Math.max(0, (cur - p) / dt) }
  })
}

export function SimpleDashboard() {
  const { data } = useCurrentMetrics(2000)
  const series = useTimeSeries(600, 3000)

  const current = data?.snapshot ?? null
  const derived = data?.derived ?? null
  const enriched = enrichedTotal(current)
  const duplicates = current?.sp.raw['stream_processor_duplicate_events_total'] ?? 0
  const retryOut = current?.sp.raw['stream_processor_retry_published_total'] ?? 0
  const errorsTotal = useMemo(() => sumStreamProcessorErrors(current?.sp.raw ?? {}), [current])

  const [eps, setEps] = useState<number>(20)
  const [dupEnabled, setDupEnabled] = useState(false)
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
        const n = c.duplicates.duplicate_every_n
        const on = n < DUPLICATE_OFF_EVERY_N / 2
        setDupEnabled(on)
        if (on) setEveryN(n)
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
  const rawEpsPoints = useMemo(() => pointsRawEventsEps(series), [series])
  const retryEpsPoints = useMemo(
    () =>
      pointsCounterDerivativeEps(series, (raw) => raw['stream_processor_retry_published_total'] ?? 0),
    [series],
  )
  const errorsEpsPoints = useMemo(
    () => pointsCounterDerivativeEps(series, (raw) => sumStreamProcessorErrors(raw)),
    [series],
  )

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
      setTimeout(() => setMsg(''), 3500)
    }
  }

  const applyDuplicates = () =>
    run('Duplicate settings', async () => {
      const step = Math.floor(everyN)
      const safe = Number.isFinite(step) && step >= 1 ? step : 12
      const n = dupEnabled ? Math.min(1_000_000, safe) : DUPLICATE_OFF_EVERY_N
      await api.setProducerDuplicates(n)
    })

  return (
    <div className="space-y-4">
      <div className="border border-black bg-white px-3 py-2">
        <div className="text-sm font-bold text-black">Pipeline Dashboard (simple)</div>
        <div className="text-xs text-black">
          Demo compose keeps user 123 absent and injects occasional bad raw payloads so retry and
          error rates are visible without clicking anything. Refresh if you just started the stack.
        </div>
        <details className="mt-2 border-t border-black pt-2 text-xs text-black">
          <summary className="cursor-pointer font-semibold text-black select-none">
            When retries vs errors fire (and how this maps to the problem statement)
          </summary>
          <ul className="mt-2 list-disc space-y-1.5 pl-4">
            <li>
              <span className="font-semibold">Retries chart</span> (derivative of{' '}
              <code className="font-mono">stream_processor_retry_published_total</code>): the stream
              processor increments this when it <span className="font-semibold">successfully parsed</span>{' '}
              a raw event but <span className="font-semibold">could not resolve the user</span> (HTTP 404
              / timeouts after its own HTTP retries). It then publishes a retry envelope to{' '}
              <span className="font-semibold">retry-events</span> instead of enriched-events.
            </li>
            <li>
              <span className="font-semibold">Errors chart</span> (sum of{' '}
              <code className="font-mono">stream_processor_errors_total</code> labels): incremented on{' '}
              <span className="font-semibold">unexpected exceptions</span> while handling raw-events
              (e.g. corrupt payload) or user-updates. It is not used for the “missing user” path—that
              path is a retry, not a counted error.
            </li>
            <li>
              <span className="font-semibold">Retry worker</span> (not this chart): consumes{' '}
              <span className="font-semibold">retry-events</span>, merges user data, and republishes with
              exponential backoff until success or <span className="font-semibold">up to 3 attempts</span>{' '}
              (<code className="font-mono">MAX_RETRY_ATTEMPTS</code>), then DLQ if still failing.
            </li>
            <li>
              <span className="font-semibold">Idempotency</span>: duplicate raw event IDs hit Redis state
              and increment <code className="font-mono">stream_processor_duplicate_events_total</code>.
            </li>
            <li>
              <span className="font-semibold">Result service</span>: consumes{' '}
              <span className="font-semibold">enriched-events</span> and stores outcomes (see result-service
              metrics/API), separate from these graphs.
            </li>
          </ul>
        </details>
      </div>

      <div className="border border-black bg-white p-3">
        <div className="text-sm font-semibold text-black">Controls</div>

        <div className="mt-2 border border-black p-3">
          <div className="text-xs font-semibold uppercase tracking-wide text-black">Stream speed</div>
          <div className="mt-2 flex items-center gap-3">
            <input
              type="range"
              min={1}
              max={1000}
              value={eps}
              onChange={(e) => setEps(Number(e.target.value))}
              className="w-full"
            />
            <div className="w-20 shrink-0 text-right font-mono text-sm text-black">{eps} eps</div>
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
            Apply speed
          </button>
        </div>

        <details className="mt-3 border border-black p-3">
          <summary className="cursor-pointer text-sm font-semibold text-black select-none">
            Advanced
          </summary>
          <div className="mt-3 space-y-4 border-t border-black pt-3">
            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-black">
                Duplicate events (producer)
              </div>
              <p className="mt-1 text-xs text-black">
                When enabled, the producer repeats an event id every N messages so the processor can
                exercise idempotency. When disabled, duplicates are turned off at the producer.
              </p>
              <label className="mt-2 flex cursor-pointer items-center gap-2 text-sm text-black">
                <input
                  type="checkbox"
                  checked={dupEnabled}
                  onChange={(e) => setDupEnabled(e.target.checked)}
                  className="h-4 w-4 border border-black accent-black"
                />
                <span>Send duplicate event IDs</span>
              </label>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <span className="text-sm text-black">Every</span>
                <input
                  type="number"
                  min={1}
                  max={1000000}
                  value={everyN}
                  disabled={!dupEnabled}
                  onChange={(e) => setEveryN(Number(e.target.value))}
                  className="w-24 border border-black px-2 py-1 font-mono text-sm text-black disabled:opacity-50"
                />
                <span className="text-sm text-black">events</span>
                <button
                  disabled={busy !== null}
                  className="border border-black bg-white px-2 py-1 text-sm font-semibold text-black disabled:opacity-50"
                  onClick={() => void applyDuplicates()}
                >
                  Apply duplicate settings
                </button>
              </div>
            </div>

            <div>
              <div className="text-xs font-semibold uppercase tracking-wide text-black">
                Retries & failures (demo)
              </div>
              <p className="mt-1 text-xs text-black">
                In docker compose demo, user 123 is already absent so you should see retries without
                clicking. &quot;Simulate user failure&quot; deletes user 123 if present; &quot;Restore&quot;
                recreates them so those events enrich again.
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                <button
                  disabled={busy !== null}
                  className="border border-black bg-white px-2 py-1 text-sm font-semibold text-black disabled:opacity-50"
                  onClick={() => run('Simulate user failure', () => api.simulateDown())}
                >
                  Simulate user failure (retries)
                </button>
                <button
                  disabled={busy !== null}
                  className="border border-black bg-white px-2 py-1 text-sm font-semibold text-black disabled:opacity-50"
                  onClick={() => run('Restore user 123', () => api.restoreUser())}
                >
                  Restore user 123
                </button>
              </div>
            </div>
          </div>
        </details>

        {msg && <div className="mt-3 font-mono text-xs text-black">{msg}</div>}
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
        <SimpleLineChart title="Raw events / sec" points={rawEpsPoints} unit="" />
      </div>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <SimpleLineChart title="Retries / sec" points={retryEpsPoints} unit="" />
        <SimpleLineChart title="Errors / sec" points={errorsEpsPoints} unit="" />
      </div>
    </div>
  )
}
