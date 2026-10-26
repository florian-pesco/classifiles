import { useState } from 'react'
import type { ReviewBatch } from '../api'

const KIND_LABEL: Record<ReviewBatch['kind'], string> = {
  name: 'Same name pattern',
  content: 'Similar content',
  proposal: 'Same suggestion',
  doubt: 'Same doubt',
}
const SHOWN = 6

/**
 * Suggested batches above the Review list. Clicking one shows only its files, ready to be
 * selected and decided together.
 */
export function BatchPanel({ batches, active, onPick }: {
  batches: ReviewBatch[]
  active: string | null
  onPick: (b: ReviewBatch | null) => void
}) {
  const [all, setAll] = useState(false)
  const [hidden, setHidden] = useState(false)
  if (batches.length === 0) return null
  const shown = all ? batches : batches.slice(0, SHOWN)

  return (
    <section className="card batch-panel">
      <div className="batch-head">
        <b>Review together</b>
        <span className="muted small">{batches.length} group{batches.length === 1 ? '' : 's'} of similar files</span>
        <span className="grow" />
        <button className="linkish small" onClick={() => setHidden(h => !h)}>{hidden ? 'show' : 'hide'}</button>
      </div>
      {!hidden && (
        <>
          <div className="batch-grid">
            {shown.map(b => (
              <button key={b.id} className={`batch kind-${b.kind}${b.id === active ? ' active' : ''}`}
                      onClick={() => onPick(b.id === active ? null : b)} title={b.why} aria-pressed={b.id === active}>
                <span className="batch-kind small">{KIND_LABEL[b.kind]}</span>
                <span className="batch-title">{b.title}</span>
                <span className="small muted">
                  {b.undecided} to decide{b.undecided < b.size ? ` · ${b.size - b.undecided} done` : ''}
                </span>
                <span className="batch-dist small">
                  {b.uniform
                    ? <>all → {b.distribution[0]?.where}</>
                    : b.distribution.slice(0, 3).map(d => `${d.count} → ${d.where}`).join(' · ')
                      + (b.distribution.length > 3 ? ' · …' : '')}
                </span>
              </button>
            ))}
          </div>
          {batches.length > SHOWN && (
            <button className="linkish small" onClick={() => setAll(a => !a)}>
              {all ? 'show fewer' : `show all ${batches.length}`}
            </button>
          )}
        </>
      )}
    </section>
  )
}
