import type { ResultRow } from '../../api/types'

interface SimpleResultsTableProps {
  rows: ResultRow[]
}

export function SimpleResultsTable({ rows }: SimpleResultsTableProps) {
  return (
    <div className="border border-black bg-white">
      <div className="border-b border-black px-3 py-2 text-sm font-semibold text-black">
        Latest enriched results
      </div>
      <div className="overflow-x-auto">
        <table className="w-full border-collapse text-left text-sm">
          <thead>
            <tr className="border-b border-black">
              {['event', 'user', 'action', 'tier', 'enriched_at', 'source'].map((h) => (
                <th key={h} className="px-3 py-2 text-xs font-semibold uppercase tracking-wide text-black">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.event_id} className="border-b border-black last:border-b-0">
                <td className="px-3 py-2 font-mono text-xs text-black">{r.event_id.slice(0, 8)}</td>
                <td className="px-3 py-2 text-black">{r.user_id}</td>
                <td className="px-3 py-2 text-black">{r.action}</td>
                <td className="px-3 py-2 text-black">{r.tier ?? '—'}</td>
                <td className="px-3 py-2 font-mono text-xs text-black">{r.enriched_at.slice(0, 19)}</td>
                <td className="px-3 py-2 text-black">{r.source}</td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td className="px-3 py-6 text-black" colSpan={6}>
                  No enriched results yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

