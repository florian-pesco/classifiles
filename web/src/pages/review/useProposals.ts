import { useCallback, useMemo, useRef, useState } from 'react'
import type { Proposal, ReviewBatch } from '../../api'
import { type Filter, type SortKey, sortRows } from './sort'

export type ListView = { scanId: number | null; filter: Filter; search: string; sort: SortKey; batch: ReviewBatch | null }

/** The proposals of the open scan, and the rows shown for the current filter, search, sort and batch.
 *
 * The list keeps its order while you decide: rows only re-sort (or leave the current filter) when you
 * change the filter, search or sort, or the whole list is reloaded (`loadRows`: new scan, accept all,
 * apply, undo) or re-sorted on purpose (`resort`). */
export function useProposals({ scanId, filter, search, sort, batch }: ListView) {
  const [proposals, setProposals] = useState<Proposal[]>([])
  const [listVersion, setListVersion] = useState(0)
  const frozen = useRef<{ key: string; ids: number[] } | null>(null)
  const loadRows = useCallback((rows: Proposal[]) => { setProposals(rows); setListVersion(v => v + 1) }, [])
  const resort = useCallback(() => setListVersion(v => v + 1), [])

  // Merge updated proposals into the list (keeps the order).
  const merge = useCallback((updated: Proposal[]) => {
    const by = new Map(updated.map(p => [p.id, p]))
    setProposals(ps => ps.map(x => by.get(x.id) ?? x))
  }, [])

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: proposals.length, duplicates: 0 }
    for (const p of proposals) {
      c[p.status] = (c[p.status] ?? 0) + 1
      if (p.duplicate_of) c.duplicates += 1
    }
    return c
  }, [proposals])

  const viewKey = `${scanId}|${filter}|${search}|${sort}|${batch?.id ?? ''}|${listVersion}`
  const visible = useMemo(() => {
    const byId = new Map(proposals.map(p => [p.id, p]))
    if (frozen.current?.key === viewKey) {  // same view: keep the order, even if a row no longer matches
      return frozen.current.ids.map(id => byId.get(id)).filter((p): p is Proposal => !!p)
    }
    const needle = search.trim().toLowerCase()
    const inBatch = batch ? new Set(batch.ids) : null
    const rows = sortRows(proposals.filter(p => (!inBatch || inBatch.has(p.id)) &&
      (filter === 'all' || (filter === 'duplicates' ? !!p.duplicate_of : p.status === filter)) &&
      (!needle || p.name.toLowerCase().includes(needle) || (p.final_target ?? '').toLowerCase().includes(needle))), sort)
    if (rows.length || proposals.length) frozen.current = { key: viewKey, ids: rows.map(p => p.id) }
    return rows
  }, [proposals, viewKey, filter, search, sort, batch])

  return { proposals, setProposals, loadRows, resort, merge, counts, visible }
}
