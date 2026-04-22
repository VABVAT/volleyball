import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { RawSnapshot } from '../api/types'

interface LatencyHistogramProps {
  snapshot: RawSnapshot | null
}

const BUCKETS = [
  { le: '0.001', label: '1ms' },
  { le: '0.005', label: '5ms' },
  { le: '0.01', label: '10ms' },
  { le: '0.025', label: '25ms' },
  { le: '0.05', label: '50ms' },
  { le: '0.1', label: '100ms' },
  { le: '0.25', label: '250ms' },
  { le: '0.5', label: '500ms' },
  { le: '1.0', label: '1s' },
]

export function LatencyHistogram({ snapshot }: LatencyHistogramProps) {
  const raw = snapshot?.sp.raw ?? {}
  let prev = 0
  const data = BUCKETS.map(({ le, label }) => {
    const cum = raw[`stream_processor_processing_seconds_bucket{le="${le}"}`] ?? 0
    const count = Math.max(0, cum - prev)
    prev = cum
    return { label, count }
  })

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
      <h3 className="mb-3 text-sm font-semibold text-gray-300">
        Processing Latency Distribution
      </h3>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#9ca3af' }} />
          <YAxis tick={{ fontSize: 11, fill: '#9ca3af' }} />
          <Tooltip
            contentStyle={{
              backgroundColor: '#111827',
              border: '1px solid #374151',
              borderRadius: 8,
            }}
          />
          <Bar dataKey="count" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

