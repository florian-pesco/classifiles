import type { Proposal, Status } from '../../api'

export type Filter = Status | 'all' | 'duplicates'
export const FILTERS: (Status | 'all')[] = ['all', 'needs_review', 'suggested', 'accepted', 'rejected', 'moved']

export type SortKey = 'confidence' | 'confidence_asc' | 'name' | 'category' | 'kind' | 'size' | 'modified'
export const SORTS: { key: SortKey; label: string }[] = [
  { key: 'confidence', label: 'Confidence ↓' },
  { key: 'confidence_asc', label: 'Confidence ↑' },
  { key: 'name', label: 'Name' },
  { key: 'category', label: 'Category' },
  { key: 'kind', label: 'File type' },
  { key: 'size', label: 'Size' },
  { key: 'modified', label: 'Modified (newest)' },
]

/** A sorted copy; ties are broken by file name. */
export function sortRows(rows: Proposal[], key: SortKey) {
  const r = [...rows]
  const by = <T,>(f: (p: Proposal) => T, desc = false) =>
    r.sort((a, b) => {
      const x = f(a), y = f(b)
      return (x < y ? -1 : x > y ? 1 : a.name.localeCompare(b.name)) * (desc ? -1 : 1)
    })
  switch (key) {
    case 'confidence': return by(p => p.confidence, true)
    case 'confidence_asc': return by(p => p.confidence)
    case 'name': return by(p => p.name.toLowerCase())
    case 'category': return by(p => p.final_target ?? '~')
    case 'kind': return by(p => p.kind)
    case 'size': return by(p => p.size, true)
    case 'modified': return by(p => p.evidence.modified ?? '', true)
  }
}
