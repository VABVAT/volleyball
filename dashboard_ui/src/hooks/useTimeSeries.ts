import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { RawSnapshot } from '../api/types'

export function useTimeSeries(windowSec = 300, intervalMs = 3000) {
  const [data, setData] = useState<RawSnapshot[]>([])

  useEffect(() => {
    let cancelled = false
    const fetch = async () => {
      try {
        const result = await api.timeSeries(windowSec)
        if (!cancelled) setData(result)
      } catch {
        /* silent — chart just shows stale */
      }
    }
    fetch()
    const id = setInterval(fetch, intervalMs)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [windowSec, intervalMs])

  return data
}

