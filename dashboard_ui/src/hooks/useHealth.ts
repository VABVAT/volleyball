import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { HealthStatus } from '../api/types'

export function useHealth(intervalMs = 5000) {
  const [health, setHealth] = useState<HealthStatus | null>(null)

  useEffect(() => {
    let cancelled = false
    const fetch = async () => {
      try {
        const result = await api.health()
        if (!cancelled) setHealth(result)
      } catch {
        /* silent */
      }
    }
    fetch()
    const id = setInterval(fetch, intervalMs)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [intervalMs])

  return health
}

