import type { Category } from '../api'

/** Dropdown of usable categories, folders first, Delete last. */
export function CategorySelect({ value, categories, onChange, disabled, label, placeholder = '— choose —' }: {
  value: string | null
  categories: Category[]
  onChange: (name: string) => void
  disabled?: boolean
  label: string
  placeholder?: string
}) {
  const usable = categories.filter(c => c.usable)
  const folders = usable.filter(c => c.source !== 'delete')
  const del = usable.find(c => c.source === 'delete')
  const known = value == null || usable.some(c => c.name === value)
  return (
    <select value={value ?? ''} disabled={disabled} onChange={e => onChange(e.target.value)} aria-label={label}>
      {(value == null || value === '') && <option value="">{placeholder}</option>}
      {!known && <option value={value!}>{value} (unavailable)</option>}
      {folders.map(c => (
        <option key={c.name} value={c.name}>{c.name}{c.action === 'copy' ? ' (copy)' : ''}</option>
      ))}
      {del && <option value={del.name}>🗑 {del.name}</option>}
    </select>
  )
}
