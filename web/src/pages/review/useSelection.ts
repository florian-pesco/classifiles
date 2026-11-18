import { useCallback, useMemo, useRef, useState } from 'react'
import type { Proposal } from '../../api'

/** Selected rows. Click toggles one row, Shift+click selects the range from the last clicked row. */
export function useSelection(proposals: Proposal[], visible: Proposal[]) {
  const [selected, setSelected] = useState<Set<number>>(new Set())
  const lastClicked = useRef<number | null>(null)

  const toggle = (id: number, shift: boolean) => {
    // Read the anchor now: React may run the updater below only after lastClicked has moved on.
    const anchor = lastClicked.current
    lastClicked.current = id
    setSelected(sel => {
      const next = new Set(sel)
      const ids = visible.map(p => p.id)
      const from = anchor == null ? -1 : ids.indexOf(anchor)
      const to = ids.indexOf(id)
      if (shift && from >= 0 && to >= 0) {
        const on = !sel.has(id)
        for (const x of ids.slice(Math.min(from, to), Math.max(from, to) + 1)) {
          if (on) next.add(x); else next.delete(x)
        }
      } else if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }
  const clear = useCallback(() => setSelected(new Set()), [])
  const allVisibleSelected = visible.length > 0 && visible.every(p => selected.has(p.id))
  const selectAllVisible = () => setSelected(allVisibleSelected ? new Set() : new Set(visible.map(p => p.id)))
  const selectedRows = useMemo(() => proposals.filter(p => selected.has(p.id)), [proposals, selected])

  return { selected, toggle, clear, allVisibleSelected, selectAllVisible, selectedRows }
}
