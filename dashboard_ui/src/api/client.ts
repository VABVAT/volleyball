import type {
  ActivityResponse,
  CurrentMetrics,
  HealthStatus,
  RawSnapshot,
  ProducerControls,
  ProducerDuplicates,
  ProducerSpeed,
  ResultRow,
  ResultStats,
  ScenarioResult,
  UsersSummary,
} from './types'

const BASE = '' // nginx proxies /api → dashboard-api

/** Result-service proxies can be slow on large DBs; allow a long read timeout. */
const RESULT_FETCH_TIMEOUT_MS = 120_000

async function get<T>(path: string, opts?: { timeoutMs?: number }): Promise<T> {
  const timeoutMs = opts?.timeoutMs
  const ctrl = new AbortController()
  const tid =
    timeoutMs != null ? window.setTimeout(() => ctrl.abort(), timeoutMs) : undefined
  try {
    const r = await fetch(`${BASE}${path}`, { signal: ctrl.signal })
    if (!r.ok) throw new Error(`${r.status} ${r.statusText} — ${path}`)
    return r.json() as Promise<T>
  } catch (e) {
    const aborted =
      (typeof DOMException !== 'undefined' && e instanceof DOMException && e.name === 'AbortError') ||
      (e instanceof Error && e.name === 'AbortError')
    if (aborted && timeoutMs != null) {
      throw new Error(`Request timed out after ${timeoutMs}ms — ${path}`)
    }
    throw e
  } finally {
    if (tid !== undefined) window.clearTimeout(tid)
  }
}

async function post<T>(
  path: string,
  params?: Record<string, string | number>,
): Promise<T> {
  const url = new URL(`${location.origin}${BASE}${path}`)
  if (params) {
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, String(v)))
  }
  const r = await fetch(url.toString(), { method: 'POST' })
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} — ${path}`)
  return r.json() as Promise<T>
}

export const api = {
  currentMetrics: () => get<CurrentMetrics>('/api/metrics/current'),
  timeSeries: (window = 300) =>
    get<RawSnapshot[]>(`/api/metrics/timeseries?window=${window}`),
  health: () => get<HealthStatus>('/api/health'),
  results: (limit = 50) =>
    get<ResultRow[]>(`/api/results?limit=${limit}`, { timeoutMs: RESULT_FETCH_TIMEOUT_MS }),
  resultStats: () => get<ResultStats>('/api/results/stats', { timeoutMs: RESULT_FETCH_TIMEOUT_MS }),
  deleteDemoUser: (userId: 1 | 2 | 3 | 123) =>
    post<ScenarioResult>(`/api/scenarios/demo-users/${userId}/delete`),
  restoreDemoUser: (userId: 1 | 2 | 3 | 123) =>
    post<ScenarioResult>(`/api/scenarios/demo-users/${userId}/restore`),
  loadBurst: (rate = 200, duration = 10) =>
    post<ScenarioResult>('/api/scenarios/load-burst', { rate, duration }),
  replayDlq: (limit = 100) => post<ScenarioResult>('/api/scenarios/replay-dlq', { limit }),
  producerControls: () => get<ProducerControls>('/api/controls/producer'),
  setProducerSpeed: (eps: number) =>
    post<ProducerSpeed>('/api/controls/producer/speed', { eps }),
  setProducerDuplicates: (every_n: number) =>
    post<ProducerDuplicates>('/api/controls/producer/duplicates', { every_n }),
  usersSummary: () => get<UsersSummary>('/api/users/summary'),
  activity: (limit = 200) => get<ActivityResponse>(`/api/activity?limit=${limit}`),
}

