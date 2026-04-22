import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { CurrentMetrics } from '../api/types'

export function useCurrentMetrics(intervalMs = 2000) {
  const [data, setData] = useState<CurrentMetrics | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    const fetch = async () => {
      try {
        const result = await api.currentMetrics()
        if (!cancelled) {
          setData(result)
          setError(null)
        }
      } catch (e) {
        if (!cancelled) setError(String(e))
      }
    }
    fetch()
    const id = setInterval(fetch, intervalMs)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [intervalMs])

  return { data, error }
}

