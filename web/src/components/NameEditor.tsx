import { useState } from 'react'

/**
 * File name line of a Review row:
 *   Name: Rechnung (1).pdf  → suggested: BeispielEnergie_Rechnung_2025-03-14.pdf  use · edit…
 *   Name: BeispielEnergie_Rechnung_2025-03-14.pdf  renamed · keep original · edit…
 * The original name is kept unless a name is chosen.
 */
export function NameEditor({ original, suggested, current, reason, disabled, onUse, onKeep, onSet }: {
  original: string
  suggested: string | null
  current: string | null  // chosen new name; null = keep the original
  reason: string | null
  disabled: boolean
  onUse: () => void
  onKeep: () => void
  onSet: (name: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState('')
  const shown = current ?? original
  const offer = suggested && suggested !== shown && !disabled

  const start = () => { setValue(shown); setEditing(true) }
  const save = () => { setEditing(false); if (value.trim() && value.trim() !== shown) onSet(value.trim()) }

  return (
    <div className="name-editor small">
      <span className="muted">Name:</span>
      {editing ? (
        <span className="row-form name-input">
          <input autoFocus value={value} onChange={e => setValue(e.target.value)} aria-label="New file name"
                 onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setEditing(false) }} />
          <button onClick={() => setEditing(false)}>Cancel</button>
          <button className="primary" onClick={save}>Use</button>
        </span>
      ) : (
        <>
          <span className={`mono name-current${current ? ' renamed' : ''}`} title={current ? `was: ${original}` : undefined}>
            {shown}
          </span>
          {current && <span className="badge new">renamed</span>}
          {offer && (
            <>
              <span className="sep">→</span>
              <button className="chip suggest-name" onClick={onUse} title={reason ? `Suggested: ${reason}` : 'Suggested name'}>
                {suggested}
              </button>
            </>
          )}
          {!disabled && (
            <>
              {current && <button className="linkish" onClick={onKeep}>keep original</button>}
              <button className="linkish" onClick={start}>edit…</button>
            </>
          )}
        </>
      )}
    </div>
  )
}
