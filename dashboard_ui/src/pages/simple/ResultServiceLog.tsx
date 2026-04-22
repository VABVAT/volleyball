import { useCallback, useEffect, useState } from 'react'
import { api } from '../../api/client'
import type { ResultRow, ResultStats } from '../../api/types'

const POLL_MS = 7000
const FETCH_LIMIT = 120

function formatWhen(iso: string): string {
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return iso
    return d.toLocaleString(undefined, {
      dateStyle: 'medium',
      timeStyle: 'medium',
    })
  } catch {
    return iso
  }
}

function shortId(id: string): string {
  if (id.length <= 16) return id
  return `${id.slice(0, 8)}…${id.slice(-6)}`
}

export function ResultServiceLog() {
  const [rows, setRows] = useState<ResultRow[]>([])
  const [stats, setStats] = useState<ResultStats | null>(null)
  const [err, setErr] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [r, s] = await Promise.all([api.results(FETCH_LIMIT), api.resultStats()])
      setRows(r)
      setStats(s)
      setErr(null)
    } catch (e) {
      setErr(String(e))
    }
  }, [])

  useEffect(() => {
    void refresh()
    const id = window.setInterval(() => void refresh(), POLL_MS)
    return () => window.clearInterval(id)
  }, [refresh])

  return (
    <div className="space-y-4">
      <div className="border border-black bg-white p-3">
        <div className="text-sm font-semibold text-black">Result service receive log</div>
        <p className="mt-1 text-xs text-black">
          Each block is one <span className="font-semibold">enriched-events</span> message the result
          service consumed (read committed), decoded, and upserted into Postgres{' '}
          <code className="font-mono text-[11px]">enriched_results</code>. Newest first. Polls every{' '}
          ~{POLL_MS / 1000}s (last {FETCH_LIMIT} rows).
        </p>
        {stats && (
          <p className="mt-2 border-t border-black pt-2 font-mono text-xs text-black">
            DB totals: <span className="font-semibold">{stats.total}</span> rows · by source{' '}
            {Object.entries(stats.by_source)
              .map(([k, v]) => `${k}=${v}`)
              .join(' · ') || '—'}{' '}
            · by action {Object.entries(stats.by_action).map(([k, v]) => `${k}=${v}`).join(' · ') || '—'}
          </p>
        )}
        {err && (
          <p className="mt-2 border border-red-700 bg-red-50 px-2 py-1 font-mono text-xs text-red-900">
            {err}
          </p>
        )}
      </div>

      <div className="space-y-2" aria-live="polite">
        {rows.length === 0 && !err ? (
          <div className="border border-dashed border-black px-3 py-6 text-center text-sm text-black">
            No rows yet. Produce traffic and wait for the stream processor to write to enriched-events.
          </div>
        ) : (
          rows.map((row) => (
            <article
              key={`${row.event_id}-${row.enriched_at}`}
              className="border border-black bg-white p-3 font-mono text-xs leading-relaxed text-black"
            >
              <div className="border-b border-black pb-2 text-[11px] font-semibold uppercase tracking-wide text-black">
                Received and persisted
              </div>
              <dl className="mt-2 grid gap-1 sm:grid-cols-2">
                <div className="flex gap-2 sm:col-span-2">
                  <dt className="w-28 shrink-0 text-black/70">enriched_at</dt>
                  <dd className="min-w-0 break-all text-black">{formatWhen(row.enriched_at)}</dd>
                </div>
                <div className="flex gap-2 sm:col-span-2">
                  <dt className="w-28 shrink-0 text-black/70">event_id</dt>
                  <dd className="min-w-0 break-all text-black" title={row.event_id}>
                    {row.event_id}
                  </dd>
                </div>
                <div className="flex gap-2">
                  <dt className="w-28 shrink-0 text-black/70">user_id</dt>
                  <dd>{row.user_id}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="w-28 shrink-0 text-black/70">user_name</dt>
                  <dd className="min-w-0 break-words">{row.user_name ?? '—'}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="w-28 shrink-0 text-black/70">action</dt>
                  <dd>{row.action}</dd>
                </div>
                <div className="flex gap-2">
                  <dt className="w-28 shrink-0 text-black/70">tier</dt>
                  <dd>{row.tier ?? '—'}</dd>
                </div>
                <div className="flex gap-2 sm:col-span-2">
                  <dt className="w-28 shrink-0 text-black/70">source</dt>
                  <dd>
                    <span className="rounded border border-black px-1.5 py-0.5">{row.source}</span>
                    <span className="ml-2 text-black/60">(who produced the enriched payload)</span>
                  </dd>
                </div>
              </dl>
              <div className="mt-2 border-t border-black pt-2 text-[11px] text-black/70">
                One-line: {formatWhen(row.enriched_at)} · {shortId(row.event_id)} · user {row.user_id}{' '}
                {row.user_name ? `(${row.user_name})` : ''} · {row.action} · tier {row.tier ?? '—'} · from{' '}
                {row.source}
              </div>
            </article>
          ))
        )}
      </div>
    </div>
  )
}
