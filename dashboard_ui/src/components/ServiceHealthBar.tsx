import type { HealthStatus } from '../api/types'

interface ServiceHealthBarProps {
  health: HealthStatus | null
}

const SERVICE_LABELS: [keyof HealthStatus, string][] = [
  ['stream_processor', 'Stream Processor'],
  ['retry_worker', 'Retry Worker'],
  ['dlq_handler', 'DLQ Handler'],
  ['user_service', 'User Service'],
  ['result_service', 'Result Service'],
]

export function ServiceHealthBar({ health }: ServiceHealthBarProps) {
  return (
    <div className="flex flex-wrap gap-3">
      {SERVICE_LABELS.map(([key, label]) => {
        const up = health ? health[key] : null
        const color = up === null ? 'bg-gray-600' : up ? 'bg-green-500' : 'bg-red-500'
        return (
          <div key={key} className="flex items-center gap-1.5">
            <span className={`h-2.5 w-2.5 animate-pulse rounded-full ${color}`} />
            <span className="text-xs text-gray-300">{label}</span>
          </div>
        )
      })}
    </div>
  )
}

