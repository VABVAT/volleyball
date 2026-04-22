import { ScenarioPanel } from '../components/ScenarioPanel'

export function Scenarios() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-gray-100">Scenarios</h2>
        <p className="mt-1 text-sm text-gray-400">
          Trigger test cases and failure conditions, then watch the charts update in real time.
        </p>
      </div>
      <ScenarioPanel />
    </div>
  )
}

