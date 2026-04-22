import { useState } from 'react'
import type { ResultRow } from '../api/types'

interface ResultsTableProps {
  rows: ResultRow[]
}

const ACTIONS = ['all', 'click', 'view', 'purchase', 'signup']
const TIERS = ['all', 'pro', 'standard']
const SOURCES = ['all', 'stream-processor', 'retry-worker']

export function ResultsTable({ rows }: ResultsTableProps) {
  const [actionFilter, setActionFilter] = useState('all')
  const [tierFilter, setTierFilter] = useState('all')
  const [sourceFilter, setSourceFilter] = useState('all')

  const filtered = rows.filter(
    (r) =>
      (actionFilter === 'all' || r.action === actionFilter) &&
      (tierFilter === 'all' || r.tier === tierFilter) &&
      (sourceFilter === 'all' || r.source === sourceFilter),
  )

  const select = (value: string, onChange: (v: string) => void, options: string[]) => (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="rounded border border-gray-700 bg-gray-800 px-2 py-1 text-xs text-gray-300"
    >
      {options.map((o) => (
        <option key={o} value={o}>
          {o}
        </option>
      ))}
    </select>
  )

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-xs text-gray-400">Filter:</span>
        {select(actionFilter, setActionFilter, ACTIONS)}
        {select(tierFilter, setTierFilter, TIERS)}
        {select(sourceFilter, setSourceFilter, SOURCES)}
        <span className="text-xs text-gray-500">{filtered.length} rows</span>
      </div>
      <div className="overflow-x-auto rounded-xl border border-gray-800">
        <table className="w-full text-left text-sm">
          <thead className="bg-gray-900 text-xs uppercase text-gray-400">
            <tr>
              {['Event ID', 'User ID', 'Action', 'Name', 'Tier', 'Enriched At', 'Source'].map(
                (h) => (
                  <th key={h} className="px-4 py-3 font-medium">
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800">
            {filtered.map((row) => (
              <tr key={row.event_id} className="bg-gray-950 transition-colors hover:bg-gray-900">
                <td className="px-4 py-2 font-mono text-xs text-gray-500">
                  {row.event_id.slice(0, 8)}…
                </td>
                <td className="px-4 py-2 text-gray-300">{row.user_id}</td>
                <td className="px-4 py-2">
                  <span className="rounded-full bg-blue-900 px-2 py-0.5 text-xs font-medium text-blue-200">
                    {row.action}
                  </span>
                </td>
                <td className="px-4 py-2 text-gray-300">{row.user_name ?? '—'}</td>
                <td className="px-4 py-2">
                  {row.tier === 'pro' ? (
                    <span className="rounded-full bg-purple-900 px-2 py-0.5 text-xs font-medium text-purple-200">
                      pro
                    </span>
                  ) : (
                    <span className="text-xs text-gray-500">{row.tier ?? '—'}</span>
                  )}
                </td>
                <td className="px-4 py-2 text-xs text-gray-500">
                  {row.enriched_at.slice(0, 19)}
                </td>
                <td className="px-4 py-2 text-xs text-gray-500">{row.source}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {filtered.length === 0 && (
          <p className="py-8 text-center text-sm text-gray-600">
            No results match the current filters.
          </p>
        )}
      </div>
    </div>
  )
}

