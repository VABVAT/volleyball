import { Route, Routes } from 'react-router-dom'
import { NavBar } from './components/NavBar'
import { useHealth } from './hooks/useHealth'
import { FaultTolerance } from './pages/FaultTolerance'
import { Overview } from './pages/Overview'
import { Pipeline } from './pages/Pipeline'
import { Results } from './pages/Results'
import { Scenarios } from './pages/Scenarios'

export function App() {
  const health = useHealth()

  return (
    <div className="min-h-screen bg-gray-950">
      <NavBar health={health} />
      <main className="mx-auto max-w-screen-xl px-6 py-6">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/pipeline" element={<Pipeline />} />
          <Route path="/fault-tolerance" element={<FaultTolerance />} />
          <Route path="/scenarios" element={<Scenarios />} />
          <Route path="/results" element={<Results />} />
        </Routes>
      </main>
    </div>
  )
}

