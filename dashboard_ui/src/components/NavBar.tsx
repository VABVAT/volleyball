import { NavLink } from 'react-router-dom'
import type { HealthStatus } from '../api/types'
import { ServiceHealthBar } from './ServiceHealthBar'

interface NavBarProps {
  health: HealthStatus | null
}

const NAV_LINKS = [
  { to: '/', label: 'Overview' },
  { to: '/pipeline', label: 'Pipeline' },
  { to: '/fault-tolerance', label: 'Fault Tolerance' },
  { to: '/scenarios', label: 'Scenarios' },
  { to: '/results', label: 'Results' },
]

export function NavBar({ health }: NavBarProps) {
  return (
    <header className="sticky top-0 z-50 border-b border-gray-800 bg-gray-900 px-6 py-3">
      <div className="mx-auto flex max-w-screen-xl items-center justify-between gap-6">
        <div className="flex items-center gap-6">
          <h1 className="whitespace-nowrap text-sm font-bold uppercase tracking-widest text-blue-400">
            Kafka Pipeline
          </h1>
          <nav className="flex gap-1">
            {NAV_LINKS.map(({ to, label }) => (
              <NavLink
                key={to}
                to={to}
                end={to === '/'}
                className={({ isActive }) =>
                  `rounded-lg px-3 py-1.5 text-sm font-medium transition-colors ${
                    isActive
                      ? 'bg-blue-600 text-white'
                      : 'text-gray-400 hover:bg-gray-800 hover:text-white'
                  }`
                }
              >
                {label}
              </NavLink>
            ))}
          </nav>
        </div>
        <ServiceHealthBar health={health} />
      </div>
    </header>
  )
}

