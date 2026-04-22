export interface ServiceSnapshot {
  raw: Record<string, number>
  reachable: boolean
}

export interface RawSnapshot {
  ts: number
  sp: ServiceSnapshot
  rw: ServiceSnapshot
  dq: ServiceSnapshot
  us: ServiceSnapshot
  rs: ServiceSnapshot
}

export interface DerivedMetrics {
  throughput_eps: number | null
  success_rate_pct: number | null
  avg_latency_ms: number | null
  retry_backlog: number
}

export interface CurrentMetrics {
  snapshot: RawSnapshot
  derived: DerivedMetrics
}

export interface HealthStatus {
  stream_processor: boolean
  retry_worker: boolean
  dlq_handler: boolean
  user_service: boolean
  result_service: boolean
}

export interface ResultRow {
  event_id: string
  user_id: number
  action: string
  user_name: string | null
  tier: string | null
  enriched_at: string
  source: string
}

export interface ResultStats {
  total: number
  by_action: Record<string, number>
  by_tier: Record<string, number>
  by_source: Record<string, number>
}

export interface ScenarioResult {
  ok?: boolean
  message?: string
  published?: number
  replayed?: number
  duration_sec?: number
  user?: Record<string, unknown>
}

