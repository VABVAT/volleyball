import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { RawSnapshot } from '../api/types'

interface EnrichmentFunnelProps {
  snapshot: RawSnapshot | null
}

const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444']

export function EnrichmentFunnel({ snapshot }: EnrichmentFunnelProps) {
  const sp = snapshot?.sp.raw ?? {}
  const dq = snapshot?.dq.raw ?? {}

  const data = [
    { label: 'Consumed', value: sp['stream_processor_events_consumed_total'] ?? 0 },
    { label: 'Enriched', value: sp['stream_processor_enriched_events_total'] ?? 0 },
    { label: 'Retry Out', value: sp['stream_processor_retry_published_total'] ?? 0 },
    { label: 'DLQ', value: dq['dlq_handler_messages_total'] ?? 0 },
  ]

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
      <h3 className="mb-3 text-sm font-semibold text-gray-300">Pipeline Funnel (totals)</h3>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey="label" tick={{ fontSize: 12, fill: '#9ca3af' }} />
          <YAxis tick={{ fontSize: 11, fill: '#9ca3af' }} />
          <Tooltip
            contentStyle={{
              backgroundColor: '#111827',
              border: '1px solid #374151',
              borderRadius: 8,
            }}
          />
          <Bar dataKey="value" radius={[4, 4, 0, 0]}>
            {data.map((_, idx) => (
              <Cell key={idx} fill={COLORS[idx]} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

