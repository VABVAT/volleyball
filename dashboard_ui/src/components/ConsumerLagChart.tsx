import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { RawSnapshot } from '../api/types'

interface ConsumerLagChartProps {
  snapshot: RawSnapshot | null
}

function getLag(raw: Record<string, number>, topic: string, partition: number): number {
  const key = `stream_processor_consumer_lag_messages{partition="${partition}",topic="${topic}"}`
  const key2 = `stream_processor_consumer_lag_messages{topic="${topic}",partition="${partition}"}`
  return raw[key] ?? raw[key2] ?? 0
}

function getRetryLag(raw: Record<string, number>, partition: number): number {
  const key = `retry_worker_consumer_lag_messages{partition="${partition}",topic="retry-events"}`
  const key2 = `retry_worker_consumer_lag_messages{topic="retry-events",partition="${partition}"}`
  return raw[key] ?? raw[key2] ?? 0
}

export function ConsumerLagChart({ snapshot }: ConsumerLagChartProps) {
  const data = [0, 1, 2].map((p) => ({
    partition: `p${p}`,
    'raw-events': snapshot ? getLag(snapshot.sp.raw, 'raw-events', p) : 0,
    'retry-events': snapshot ? getRetryLag(snapshot.rw.raw, p) : 0,
  }))

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
      <h3 className="mb-3 text-sm font-semibold text-gray-300">Consumer Lag (messages)</h3>
      <ResponsiveContainer width="100%" height={200}>
        <BarChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey="partition" tick={{ fontSize: 12, fill: '#9ca3af' }} />
          <YAxis tick={{ fontSize: 11, fill: '#9ca3af' }} />
          <Tooltip
            contentStyle={{
              backgroundColor: '#111827',
              border: '1px solid #374151',
              borderRadius: 8,
            }}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Bar dataKey="raw-events" fill="#3b82f6" radius={[4, 4, 0, 0]} />
          <Bar dataKey="retry-events" fill="#f59e0b" radius={[4, 4, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

