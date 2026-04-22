import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { ResultRow, ResultStats } from '../api/types'

export function useResults(intervalMs = 5000) {
  const [rows, setRows] = useState<ResultRow[]>([])
  const [stats, setStats] = useState<ResultStats | null>(null)

  useEffect(() => {
    let cancelled = false
    const fetch = async () => {
      try {
        const [r, s] = await Promise.all([api.results(50), api.resultStats()])
        if (!cancelled) {
          setRows(r)
          setStats(s)
        }
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

  return { rows, stats }
}

