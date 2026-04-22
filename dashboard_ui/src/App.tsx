import { useState } from 'react'
import { SimpleDashboard } from './pages/simple/SimpleDashboard'
import { ResultServiceLog } from './pages/simple/ResultServiceLog'

type MainTab = 'pipeline' | 'results-log'

export function App() {
  const [tab, setTab] = useState<MainTab>('pipeline')

  return (
    <div className="min-h-screen bg-white">
      <div className="border-b border-black bg-white">
        <div className="mx-auto flex max-w-screen-xl flex-wrap gap-2 px-4 py-3">
          <button
            type="button"
            onClick={() => setTab('pipeline')}
            className={`border border-black px-3 py-1.5 text-sm font-semibold transition-colors ${
              tab === 'pipeline' ? 'bg-black text-white' : 'bg-white text-black hover:bg-neutral-100'
            }`}
          >
            Pipeline
          </button>
          <button
            type="button"
            onClick={() => setTab('results-log')}
            className={`border border-black px-3 py-1.5 text-sm font-semibold transition-colors ${
              tab === 'results-log' ? 'bg-black text-white' : 'bg-white text-black hover:bg-neutral-100'
            }`}
          >
            Result service log
          </button>
        </div>
      </div>
      <main className="mx-auto max-w-screen-xl p-4">
        {tab === 'pipeline' ? <SimpleDashboard /> : <ResultServiceLog />}
      </main>
    </div>
  )
}

