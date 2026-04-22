import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../../api/client'
import { useCurrentMetrics } from '../../hooks/useCurrentMetrics'
import { useTimeSeries } from '../../hooks/useTimeSeries'
import { SimpleLineChart, type SimplePoint } from '../../components/simple/SimpleLineChart'
import { SimpleStatCard } from '../../components/simple/SimpleStatCard'
import type { RawSnapshot } from '../../api/types'

/** Producer treats very large N as effectively no duplicates. */
const DUPLICATE_OFF_EVERY_N = 1_000_000

/** User-service admin allows delete/restore only for these ids. */
const DEMO_USER_IDS = [1, 2, 3, 123] as const
type DemoUserId = (typeof DEMO_USER_IDS)[number]

function enrichedTotal(snapshot: RawSnapshot | null): number {
  return snapshot?.sp.raw['stream_processor_enriched_events_total'] ?? 0
}

function sumStreamProcessorErrors(raw: Record<string, number>): number {
  return Object.entries(raw)
    .filter(([k]) => k.startsWith('stream_processor_errors_total'))
    .reduce((acc, [, v]) => acc + (typeof v === 'number' ? v : 0), 0)
}

/** Sum consumer lag across raw-events partitions (topic has 3 partitions in compose). */
function sumRawEventsLag(raw: Record<string, number>): number {
  const topic = 'raw-events'
  let s = 0
  for (let p = 0; p < 3; p++) {
    const key = `stream_processor_consumer_lag_messages{partition="${p}",topic="${topic}"}`
    const key2 = `stream_processor_consumer_lag_messages{topic="${topic}",partition="${p}"}`
    s += raw[key] ?? raw[key2] ?? 0
  }
  return s
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

/**
 * Rate of raw-events intake (counter is messages consumed from the topic).
 * Matches the producer slider only when consumer lag is near zero; otherwise the processor
 * drains backlog as fast as it can, so this can stay high while lag is large.
 */
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

/** Each retry-worker consume is one pass over a retry envelope (up to MAX_RETRY_ATTEMPTS, default 3). */
function pointsRetryWorkerConsumedEps(series: RawSnapshot[]): SimplePoint[] {
  const key = 'retry_worker_messages_consumed_total'
  return series.map((s, i) => {
    const prev = series[i - 1]
    if (!prev || !s.rw.reachable || !prev.rw.reachable) return { t: s.ts, v: null }
    const dt = s.ts - prev.ts
    if (dt <= 0) return { t: s.ts, v: null }
    const cur = s.rw.raw[key] ?? 0
    const p = prev.rw.raw[key] ?? 0
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
  const rawBacklogMsgs = useMemo(() => {
    if (!current?.sp.reachable) return null
    return sumRawEventsLag(current.sp.raw)
  }, [current])

  const [eps, setEps] = useState<number>(20)
  const [dupEnabled, setDupEnabled] = useState(false)
  const [everyN, setEveryN] = useState<number>(12)
  const [busy, setBusy] = useState<string | null>(null)
  const [msg, setMsg] = useState<string>('')
  const [userSummary, setUserSummary] = useState<{ user_count: number; canonical_total: number } | null>(
    null,
  )
  const [logLines, setLogLines] = useState<{ ts: number; message: string }[]>([])
  const [producerHydrated, setProducerHydrated] = useState(false)

  const refreshActivity = useCallback(async () => {
    try {
      const r = await api.activity(400)
      setLogLines(r.lines)
    } catch {
      /* ignore */
    }
  }, [])

  const refreshUsers = useCallback(async () => {
    try {
      setUserSummary(await api.usersSummary())
    } catch {
      /* ignore */
    }
  }, [])

  useEffect(() => {
    void refreshUsers()
    const id = setInterval(() => void refreshUsers(), 12000)
    return () => clearInterval(id)
  }, [refreshUsers])

  useEffect(() => {
    void refreshActivity()
    const id = setInterval(() => void refreshActivity(), 2800)
    return () => clearInterval(id)
  }, [refreshActivity])

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
      } finally {
        if (!cancelled) setProducerHydrated(true)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!producerHydrated) return
    const t = window.setTimeout(() => {
      void api.setProducerSpeed(eps).catch(() => {})
    }, 450)
    return () => window.clearTimeout(t)
  }, [eps, producerHydrated])

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
  const retryWorkerEpsPoints = useMemo(() => pointsRetryWorkerConsumedEps(series), [series])
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
      void refreshActivity()
      void refreshUsers()
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
          <p className="mt-2 text-xs text-black">
            This sets how many <span className="font-semibold">new</span> events/sec the producer writes.
            Enriched/sec stays high while <span className="font-semibold">raw-events</span> has a large
            backlog (see backlog stat): the stream processor drains the topic as fast as it can until lag
            is near zero. Speed updates after a short pause while you drag the slider.
          </p>
        </div>

        <details className="mt-3 border border-black p-3">
          <summary className="cursor-pointer text-sm font-semibold text-black select-none">
            Advanced
          </summary>
          <div className="mt-3 space-y-4 border-t border-black pt-3">
            <details className="text-xs text-black">
              <summary className="cursor-pointer font-semibold text-black select-none">
                When retries vs errors fire (and how this maps to the problem statement)
              </summary>
              <ul className="mt-2 list-disc space-y-1.5 pl-4">
                <li>
                  <span className="font-semibold">Delete demo user</span> (users 1, 2, 3, or 123) removes
                  that row and Redis cache. Events for that user id then cannot be enriched: the stream
                  processor increments{' '}
                  <code className="font-mono">stream_processor_errors_total{'{'}stage=&quot;user_unavailable&quot;{'}'}</code>{' '}
                  (counted on the <span className="font-semibold">Errors</span> chart) and publishes to{' '}
                  <span className="font-semibold">retry-events</span> (also increments{' '}
                  <code className="font-mono">stream_processor_retry_published_total</code> once per event).
                </li>
                <li>
                  <span className="font-semibold">Retries chart</span> (derivative of{' '}
                  <code className="font-mono">retry_worker_messages_consumed_total</code>): each consume is
                  one pass. The worker <span className="font-semibold">waits until</span>{' '}
                  <code className="font-mono">retry_after</code> (≥1s exponential backoff between attempts,
                  first hop delayed by the stream processor) instead of tight requeue loops.
                </li>
                <li>
                  <span className="font-semibold">Errors chart</span> (sum of{' '}
                  <code className="font-mono">stream_processor_errors_total</code>): includes{' '}
                  <code className="font-mono">user_unavailable</code> (missing user after simulate) plus true
                  failures such as <code className="font-mono">raw_event</code> exceptions and{' '}
                  <code className="font-mono">user_update</code> handling errors.
                </li>
                <li>
                  <span className="font-semibold">Idempotency</span>: duplicate raw event IDs hit Redis state
                  and increment <code className="font-mono">stream_processor_duplicate_events_total</code>.
                </li>
                <li>
                  <span className="font-semibold">Result service</span>: consumes{' '}
                  <span className="font-semibold">enriched-events</span> and stores outcomes (see
                  result-service metrics/API), separate from these graphs.
                </li>
              </ul>
            </details>

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
                Retries & failures
              </div>
              <p className="mt-1 text-xs text-black">
                Delete removes that user from Postgres and Redis projection. Restore re-upserts the
                canonical row and publishes <span className="font-semibold">user-updates</span>.
              </p>
              <div className="mt-2 text-xs font-semibold uppercase tracking-wide text-black">Delete user</div>
              <div className="mt-1 flex flex-wrap gap-2">
                {DEMO_USER_IDS.map((uid) => (
                  <button
                    key={`del-${uid}`}
                    disabled={busy !== null}
                    className="border border-black bg-white px-2 py-1 text-sm font-semibold text-black disabled:opacity-50"
                    onClick={() =>
                      run(`Delete user ${uid}`, async () => {
                        await api.deleteDemoUser(uid as DemoUserId)
                      })
                    }
                  >
                    Delete {uid}
                  </button>
                ))}
              </div>
              <div className="mt-3 text-xs font-semibold uppercase tracking-wide text-black">
                Restore user
              </div>
              <div className="mt-1 flex flex-wrap gap-2">
                {DEMO_USER_IDS.map((uid) => (
                  <button
                    key={`rst-${uid}`}
                    disabled={busy !== null}
                    className="border border-black bg-white px-2 py-1 text-sm font-semibold text-black disabled:opacity-50"
                    onClick={() =>
                      run(`Restore user ${uid}`, async () => {
                        await api.restoreDemoUser(uid as DemoUserId)
                      })
                    }
                  >
                    Restore {uid}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </details>

        {msg && <div className="mt-3 font-mono text-xs text-black">{msg}</div>}
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-5">
        <SimpleStatCard label="Enriched total" value={enriched.toFixed(0)} />
        <SimpleStatCard label="Enriched / sec (recent)" value={fmt(enrichedEpsNow, 1)} />
        <SimpleStatCard label="Enriched %" value={fmt(derived?.success_rate_pct ?? null, 1)} />
        <SimpleStatCard
          label="Raw-events backlog (msgs)"
          value={rawBacklogMsgs == null ? '—' : Math.round(rawBacklogMsgs).toLocaleString()}
        />
        <SimpleStatCard
          label="Users (DB / canonical)"
          value={
            userSummary
              ? `${userSummary.user_count} / ${userSummary.canonical_total}`
              : '—'
          }
        />
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
        <SimpleLineChart title="Retry worker / sec" points={retryWorkerEpsPoints} unit="" />
        <SimpleLineChart title="Errors / sec" points={errorsEpsPoints} unit="" />
      </div>

      <div className="border border-black bg-white p-3">
        <div className="text-sm font-semibold text-black">Activity log</div>
        <p className="mt-1 text-xs text-black">
          Each metrics scrape adds a line with raw consumed and enriched counter deltas; API actions
          (producer controls, scenarios) appear here too. Refreshes automatically.
        </p>
        <pre
          className="mt-2 max-h-56 overflow-y-auto border border-black bg-white p-2 font-mono text-[11px] leading-snug text-black whitespace-pre-wrap"
          aria-live="polite"
        >
          {logLines.length === 0
            ? 'No activity yet.'
            : logLines
                .map((line) => {
                  const t = new Date(line.ts * 1000).toLocaleTimeString([], {
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                  })
                  return `${t}  ${line.message}`
                })
                .join('\n')}
        </pre>
      </div>
    </div>
  )
}
