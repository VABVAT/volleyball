import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { RawSnapshot } from '../api/types'

interface ThroughputChartProps {
  snapshots: RawSnapshot[]
}

export function ThroughputChart({ snapshots }: ThroughputChartProps) {
  const data = snapshots.map((s, i) => {
    const prev = snapshots[i - 1]
    let eps: number | null = null
    if (prev && s.sp.reachable && prev.sp.reachable) {
      const dt = s.ts - prev.ts
      const enriched = s.sp.raw['stream_processor_enriched_events_total'] ?? 0
      const prevEnriched = prev.sp.raw['stream_processor_enriched_events_total'] ?? 0
      if (dt > 0) eps = Math.max(0, (enriched - prevEnriched) / dt)
    }
    return {
      time: new Date(s.ts * 1000).toLocaleTimeString(),
      eps,
    }
  })

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
      <h3 className="mb-3 text-sm font-semibold text-gray-300">Throughput (events/sec)</h3>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis
            dataKey="time"
            tick={{ fontSize: 11, fill: '#9ca3af' }}
            interval="preserveStartEnd"
          />
          <YAxis tick={{ fontSize: 11, fill: '#9ca3af' }} />
          <Tooltip
            contentStyle={{
              backgroundColor: '#111827',
              border: '1px solid #374151',
              borderRadius: 8,
            }}
            labelStyle={{ color: '#f9fafb' }}
          />
          <Line
            type="monotone"
            dataKey="eps"
            stroke="#3b82f6"
            strokeWidth={2}
            dot={false}
            connectNulls={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

