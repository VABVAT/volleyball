interface KpiCardProps {
  label: string
  value: string | number | null
  unit?: string
  highlight?: 'green' | 'amber' | 'red' | 'blue'
}

const highlights = {
  green: 'border-green-500 text-green-400',
  amber: 'border-amber-400 text-amber-300',
  red: 'border-red-500 text-red-400',
  blue: 'border-blue-500 text-blue-400',
}

export function KpiCard({ label, value, unit, highlight }: KpiCardProps) {
  const color = highlight ? highlights[highlight] : 'border-gray-700 text-gray-100'
  const display = value === null || value === undefined ? '—' : value

  return (
    <div className={`flex flex-col gap-1 rounded-xl border bg-gray-900 p-4 ${color}`}>
      <p className="text-xs font-medium uppercase tracking-widest text-gray-400">{label}</p>
      <p className="text-3xl font-bold">
        {display}
        {unit && <span className="ml-1 text-sm font-normal text-gray-400">{unit}</span>}
      </p>
    </div>
  )
}

