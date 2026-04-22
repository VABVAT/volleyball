import { useState } from 'react'
import { api } from '../api/client'
import { ActivityLog } from './ActivityLog'

export function ScenarioPanel() {
  const [log, setLog] = useState<string[]>([])
  const [running, setRunning] = useState<string | null>(null)

  const stamp = () => new Date().toLocaleTimeString()

  const run = async (name: string, fn: () => Promise<unknown>) => {
    setRunning(name)
    setLog((prev) => [`[${stamp()}] ▶ ${name} started...`, ...prev])
    try {
      const result = await fn()
      setLog((prev) => [`[${stamp()}] ✓ ${name} done — ${JSON.stringify(result)}`, ...prev])
    } catch (e) {
      setLog((prev) => [`[${stamp()}] ✗ ${name} failed — ${e}`, ...prev])
    } finally {
      setRunning(null)
    }
  }

  const btn = (
    label: string,
    key: string,
    onClick: () => Promise<unknown>,
    color = 'blue',
  ) => {
    const colors: Record<string, string> = {
      blue: 'bg-blue-600 hover:bg-blue-500',
      amber: 'bg-amber-600 hover:bg-amber-500',
      green: 'bg-green-600 hover:bg-green-500',
      red: 'bg-red-700 hover:bg-red-600',
    }
    return (
      <button
        key={key}
        onClick={() => run(label, onClick)}
        disabled={running !== null}
        className={`rounded-lg px-4 py-2 text-sm font-semibold text-white transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${colors[color]}`}
      >
        {running === label ? '⏳ Running…' : label}
      </button>
    )
  }

  const scenarios = [
    {
      key: 'simulate',
      label: 'Simulate User Down',
      color: 'red',
      description:
        'Deletes user 123 from the DB. Events for userId=123 will start flowing to retry → then DLQ.',
      fn: () => api.simulateDown(),
    },
    {
      key: 'restore',
      label: 'Restore User',
      color: 'green',
      description: 'Re-inserts user 123. Watch the retry queue drain and enriched events resume.',
      fn: () => api.restoreUser(),
    },
    {
      key: 'burst',
      label: 'Load Burst (10s)',
      color: 'blue',
      description:
        'Fires 200 events/sec for 10 seconds. Watch the throughput chart spike and lag recover.',
      fn: () => api.loadBurst(200, 10),
    },
    {
      key: 'replay',
      label: 'Replay DLQ',
      color: 'amber',
      description: 'Re-injects the last 100 DLQ entries into raw-events for reprocessing.',
      fn: () => api.replayDlq(100),
    },
  ]

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        {scenarios.map(({ key, label, color, description, fn }) => (
          <div key={key} className="space-y-3 rounded-xl border border-gray-800 bg-gray-900 p-5">
            <h3 className="font-semibold text-gray-100">{label}</h3>
            <p className="text-sm text-gray-400">{description}</p>
            {btn(label, key, fn, color)}
          </div>
        ))}
      </div>
      <div>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-gray-300">Activity Log</h3>
          <button
            onClick={() => setLog([])}
            className="text-xs text-gray-500 transition-colors hover:text-gray-300"
          >
            Clear
          </button>
        </div>
        <ActivityLog entries={log} />
      </div>
    </div>
  )
}

