interface SimpleStatCardProps {
  label: string
  value: string
}

export function SimpleStatCard({ label, value }: SimpleStatCardProps) {
  return (
    <div className="border border-black bg-white p-3">
      <div className="text-xs font-semibold uppercase tracking-wide text-black">{label}</div>
      <div className="mt-1 text-2xl font-bold text-black">{value}</div>
    </div>
  )
}

