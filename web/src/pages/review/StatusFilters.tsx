import { STATUS_LABEL } from '../../labels'
import { type Filter, FILTERS } from './sort'

/** Status filter with counts, plus the duplicates filter and "Review duplicates". */
export function StatusFilters({ filter, setFilter, counts, onResort, undecidedDuplicates, onCompare }: {
  filter: Filter
  setFilter: (f: Filter) => void
  counts: Record<string, number>
  onResort: () => void
  undecidedDuplicates: number
  onCompare: () => void
}) {
  return (
    <nav className="filters" aria-label="Filter by status">
      <div className="seg">
        {FILTERS.map(f => (
          <button key={f} className={f === filter ? 'active' : ''} aria-pressed={f === filter}
                  onClick={() => (f === filter ? onResort() : setFilter(f))}
                  title={f === filter ? 'Click again to re-sort the list' : undefined}>
            {f === 'all' ? 'All' : STATUS_LABEL[f]}<span className="count">{counts[f] ?? 0}</span>
          </button>
        ))}
      </div>
      {counts.duplicates > 0 && (
        <>
          <button className={filter === 'duplicates' ? 'chip active' : 'chip dup-chip'} onClick={() => setFilter('duplicates')}>
            Duplicates <span className="count">{counts.duplicates}</span>
          </button>
          {undecidedDuplicates > 0 && (
            <button className="chip dup-chip" onClick={onCompare}
                    title="Go through the undecided duplicates one by one with a side-by-side comparison">
              Review duplicates ({undecidedDuplicates}) →
            </button>
          )}
        </>
      )}
    </nav>
  )
}
