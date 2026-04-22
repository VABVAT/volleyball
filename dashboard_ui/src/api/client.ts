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

async function get<T>(path: string): Promise<T> {
  const r = await fetch(`${BASE}${path}`)
  if (!r.ok) throw new Error(`${r.status} ${r.statusText} — ${path}`)
  return r.json() as Promise<T>
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
  results: (limit = 50) => get<ResultRow[]>(`/api/results?limit=${limit}`),
  resultStats: () => get<ResultStats>('/api/results/stats'),
  simulateDown: () => post<ScenarioResult>('/api/scenarios/simulate-down'),
  restoreUser: () => post<ScenarioResult>('/api/scenarios/restore-user'),
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
  simulateRandomDeletions: (count: number) =>
    post<ScenarioResult>('/api/scenarios/simulate-random-deletions', { count }),
  restoreRandomDeletions: () => post<ScenarioResult>('/api/scenarios/restore-random-deletions'),
}

