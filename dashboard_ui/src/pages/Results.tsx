import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { KpiCard } from '../components/KpiCard'
import { ResultsTable } from '../components/ResultsTable'
import { useResults } from '../hooks/useResults'

const COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899']

export function Results() {
  const { rows, stats } = useResults()

  const actionData = stats
    ? Object.entries(stats.by_action).map(([name, value]) => ({ name, value }))
    : []

  const tierData = stats ? Object.entries(stats.by_tier).map(([name, value]) => ({ name, value })) : []

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold text-gray-100">Result Store</h2>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <KpiCard label="Total Records" value={stats?.total ?? null} highlight="blue" />
        {actionData.slice(0, 3).map(({ name, value }) => (
          <KpiCard key={name} label={`Action: ${name}`} value={value} />
        ))}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <h3 className="mb-3 text-sm font-semibold text-gray-300">By Action</h3>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={actionData}>
              <XAxis dataKey="name" tick={{ fontSize: 12, fill: '#9ca3af' }} />
              <YAxis tick={{ fontSize: 11, fill: '#9ca3af' }} />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#111827',
                  border: '1px solid #374151',
                  borderRadius: 8,
                }}
              />
              <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                {actionData.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>

        <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
          <h3 className="mb-3 text-sm font-semibold text-gray-300">By Tier</h3>
          <ResponsiveContainer width="100%" height={160}>
            <BarChart data={tierData}>
              <XAxis dataKey="name" tick={{ fontSize: 12, fill: '#9ca3af' }} />
              <YAxis tick={{ fontSize: 11, fill: '#9ca3af' }} />
              <Tooltip
                contentStyle={{
                  backgroundColor: '#111827',
                  border: '1px solid #374151',
                  borderRadius: 8,
                }}
              />
              <Bar dataKey="value" radius={[4, 4, 0, 0]}>
                {tierData.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>

      <ResultsTable rows={rows} />
    </div>
  )
}

