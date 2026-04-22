interface ActivityLogProps {
  entries: string[]
}

export function ActivityLog({ entries }: ActivityLogProps) {
  return (
    <div className="h-48 space-y-1 overflow-y-auto rounded-lg border border-gray-800 bg-gray-950 p-3 font-mono text-xs text-gray-400">
      {entries.length === 0 && (
        <p className="italic text-gray-600">No activity yet. Trigger a scenario above.</p>
      )}
      {entries.map((e, i) => (
        <p key={i} className="leading-relaxed">
          {e}
        </p>
      ))}
    </div>
  )
}

