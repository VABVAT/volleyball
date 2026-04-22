import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

export type SimplePoint = {
  t: number // epoch seconds
  v: number | null
}

interface SimpleLineChartProps {
  title: string
  points: SimplePoint[]
  unit?: string
}

function fmtTime(tSec: number) {
  const d = new Date(tSec * 1000)
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function SimpleLineChart({ title, points, unit }: SimpleLineChartProps) {
  const data = points.map((p) => ({
    time: fmtTime(p.t),
    v: p.v,
  }))

  return (
    <div className="border border-black bg-white p-3">
      <div className="mb-2 text-sm font-semibold text-black">{title}</div>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={data} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#d4d4d4" />
          <XAxis dataKey="time" tick={{ fontSize: 11, fill: '#111' }} interval="preserveStartEnd" />
          <YAxis
            tick={{ fontSize: 11, fill: '#111' }}
            width={40}
            tickFormatter={(v) => (unit ? `${v}${unit}` : String(v))}
          />
          <Tooltip
            contentStyle={{ backgroundColor: 'white', border: '1px solid black', borderRadius: 0 }}
            labelStyle={{ color: 'black' }}
            formatter={(value) => [unit ? `${value}${unit}` : value, '']}
          />
          <Line type="monotone" dataKey="v" stroke="#111" strokeWidth={2} dot={false} connectNulls={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}

