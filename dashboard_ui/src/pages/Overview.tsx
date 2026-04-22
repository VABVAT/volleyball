import { EnrichmentFunnel } from '../components/EnrichmentFunnel'
import { KpiCard } from '../components/KpiCard'
import { ThroughputChart } from '../components/ThroughputChart'
import { useCurrentMetrics } from '../hooks/useCurrentMetrics'
import { useTimeSeries } from '../hooks/useTimeSeries'

export function Overview() {
  const { data } = useCurrentMetrics()
  const series = useTimeSeries(300)

  const d = data?.derived
  const sp = data?.snapshot.sp.raw ?? {}

  const fmt = (v: number | null | undefined, decimals = 1) =>
    v == null ? null : Number(v.toFixed(decimals))

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold text-gray-100">Overview</h2>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard label="Throughput" value={fmt(d?.throughput_eps)} unit="eps" highlight="blue" />
        <KpiCard
          label="Total Enriched"
          value={sp['stream_processor_enriched_events_total']?.toFixed(0) ?? null}
          highlight="green"
        />
        <KpiCard
          label="Success Rate"
          value={fmt(d?.success_rate_pct)}
          unit="%"
          highlight={
            d?.success_rate_pct == null
              ? undefined
              : d.success_rate_pct >= 95
                ? 'green'
                : d.success_rate_pct >= 80
                  ? 'amber'
                  : 'red'
          }
        />
        <KpiCard label="Avg Latency" value={fmt(d?.avg_latency_ms)} unit="ms" highlight="blue" />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ThroughputChart snapshots={series} />
        <EnrichmentFunnel snapshot={data?.snapshot ?? null} />
      </div>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard
          label="Duplicates Skipped"
          value={sp['stream_processor_duplicate_events_total']?.toFixed(0) ?? null}
        />
        <KpiCard
          label="Retry Backlog"
          value={fmt(d?.retry_backlog, 0)}
          highlight={
            d?.retry_backlog == null
              ? undefined
              : d.retry_backlog > 100
                ? 'red'
                : d.retry_backlog > 10
                  ? 'amber'
                  : 'green'
          }
        />
        <KpiCard
          label="User Updates Applied"
          value={sp['stream_processor_user_updates_applied_total']?.toFixed(0) ?? null}
        />
        <KpiCard
          label="DLQ Total"
          value={data?.snapshot.dq.raw['dlq_handler_messages_total']?.toFixed(0) ?? null}
          highlight="red"
        />
      </div>
    </div>
  )
}

