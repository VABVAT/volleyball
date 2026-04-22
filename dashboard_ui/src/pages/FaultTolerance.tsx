import { Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'
import { KpiCard } from '../components/KpiCard'
import { useCurrentMetrics } from '../hooks/useCurrentMetrics'

export function FaultTolerance() {
  const { data } = useCurrentMetrics()

  const rw = data?.snapshot.rw.raw ?? {}
  const sp = data?.snapshot.sp.raw ?? {}
  const dq = data?.snapshot.dq.raw ?? {}

  const retrySuccess = rw['retry_worker_success_total'] ?? 0
  const retryDlq = rw['retry_worker_dlq_published_total'] ?? 0
  const total = retrySuccess + retryDlq

  const pieData = [
    { name: 'Success', value: retrySuccess },
    { name: 'Sent to DLQ', value: retryDlq },
  ]

  const errors: [string, string][] = [
    ['sp.raw_event', 'stream_processor_errors_total{stage="raw_event"}'],
    ['sp.user_update', 'stream_processor_errors_total{stage="user_update"}'],
    ['rw.handle', 'retry_worker_errors_total{stage="handle"}'],
  ]

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold text-gray-100">Fault Tolerance</h2>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard
          label="Retry Consumed"
          value={rw['retry_worker_messages_consumed_total']?.toFixed(0) ?? null}
        />
        <KpiCard label="Retry Success" value={retrySuccess.toFixed(0)} highlight="green" />
        <KpiCard label="Retry → DLQ" value={retryDlq.toFixed(0)} highlight="red" />
        <KpiCard
          label="DLQ Replayed"
          value={dq['dlq_handler_replay_total']?.toFixed(0) ?? null}
          highlight="amber"
        />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <h3 className="mb-3 text-sm font-semibold text-gray-300">Retry Outcome (totals)</h3>
          {total > 0 ? (
            <ResponsiveContainer width="100%" height={200}>
              <PieChart>
                <Pie
                  data={pieData}
                  cx="50%"
                  cy="50%"
                  innerRadius={60}
                  outerRadius={90}
                  dataKey="value"
                >
                  <Cell fill="#10b981" />
                  <Cell fill="#ef4444" />
                </Pie>
                <Tooltip
                  contentStyle={{
                    backgroundColor: '#111827',
                    border: '1px solid #374151',
                    borderRadius: 8,
                  }}
                />
                <Legend wrapperStyle={{ fontSize: 12 }} />
              </PieChart>
            </ResponsiveContainer>
          ) : (
            <p className="py-16 text-center text-sm text-gray-600">No retry data yet</p>
          )}
        </div>

        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <h3 className="mb-3 text-sm font-semibold text-gray-300">Error Counters</h3>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs uppercase text-gray-500">
                <th className="py-2 text-left">Stage</th>
                <th className="py-2 text-right">Count</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800">
              {errors.map(([label, key]) => {
                const svc = label.startsWith('sp') ? sp : rw
                const val = svc[key] ?? 0
                return (
                  <tr key={key}>
                    <td className="py-2 font-mono text-xs text-gray-400">{label}</td>
                    <td
                      className={`py-2 text-right font-bold ${
                        val > 0 ? 'text-red-400' : 'text-gray-600'
                      }`}
                    >
                      {val.toFixed(0)}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}

