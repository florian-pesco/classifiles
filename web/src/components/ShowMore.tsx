import { useState, type ReactNode } from 'react'

/** A list that shows the first `limit` items and a "Show all" button for the rest. */
export function ShowMore<T>({ items, limit = 5, render, className = 'simple-list', empty }: {
  items: T[]
  limit?: number
  render: (item: T) => ReactNode
  className?: string
  empty?: ReactNode
}) {
  const [all, setAll] = useState(false)
  if (!items.length) return <>{empty ?? null}</>
  const shown = all ? items : items.slice(0, limit)
  return (
    <>
      <ul className={className}>{shown.map(render)}</ul>
      {items.length > limit && (
        <button className="ghost show-more" onClick={() => setAll(a => !a)}>
          {all ? 'Show fewer' : `Show all ${items.length}`}
        </button>
      )}
    </>
  )
}
