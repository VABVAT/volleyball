import { ConsumerLagChart } from '../components/ConsumerLagChart'
import { LatencyHistogram } from '../components/LatencyHistogram'
import { ThroughputChart } from '../components/ThroughputChart'
import { useCurrentMetrics } from '../hooks/useCurrentMetrics'
import { useTimeSeries } from '../hooks/useTimeSeries'

export function Pipeline() {
  const { data } = useCurrentMetrics()
  const series = useTimeSeries(300)

  return (
    <div className="space-y-6">
      <h2 className="text-lg font-semibold text-gray-100">Pipeline Deep Dive</h2>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <ConsumerLagChart snapshot={data?.snapshot ?? null} />
        <LatencyHistogram snapshot={data?.snapshot ?? null} />
      </div>
      <ThroughputChart snapshots={series} />
    </div>
  )
}

