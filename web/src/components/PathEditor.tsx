import { useEffect, useState } from 'react'
import { api } from '../api'
import { Icon } from './Icon'

/**
 * Folder inside the chosen category, shown as clickable breadcrumbs:
 *   Studies › Lectures › Econ 1      click a part to shorten the path to it; "Category only" removes it
 * plus a suggested new folder (created only when chosen) and an editor with the existing subfolders.
 */
export function PathEditor({ category, subpath, suggested, newFolder, disabled, onChange }: {
  category: string
  subpath: string
  suggested: string
  newFolder: string | null
  disabled: boolean
  onChange: (subpath: string) => void
}) {
  const [editing, setEditing] = useState(false)
  const parts = subpath ? subpath.split('/') : []
  const isNew = !!newFolder && subpath === newFolder
  const offerNew = !!newFolder && subpath !== newFolder && !disabled

  return (
    <div className="path-editor small">
      <span className="muted">Folder:</span>
      <button className="crumb root" disabled={disabled || !parts.length} onClick={() => onChange('')}
              title="Put it directly into the category folder">{category}</button>
      {parts.map((part, i) => {
        const last = i === parts.length - 1
        return (
          <span key={i} className="crumb-wrap">
            <span className="sep">›</span>
            <button className={`crumb${last ? ' last' : ''}`} disabled={disabled || last}
                    onClick={() => onChange(parts.slice(0, i + 1).join('/'))}
                    title={last ? undefined : 'Shorten the path to here'}>
              {part}
            </button>
            {last && isNew && <span className="badge new">new folder</span>}
          </span>
        )
      })}
      {!disabled && (
        <>
          {parts.length > 0 && (
            <button className="linkish" onClick={() => onChange('')} title="Ignore the subfolder">category only</button>
          )}
          {subpath !== suggested && suggested && (
            <button className="linkish" onClick={() => onChange(suggested)}>use suggestion</button>
          )}
          <button className="linkish" onClick={() => setEditing(true)}>edit…</button>
        </>
      )}
      {offerNew && (
        <button className="chip suggest-new" onClick={() => onChange(newFolder!)}
                title="Create this folder when applying">
          + new folder “{newFolder!.split('/').pop()}”
        </button>
      )}
      {editing && (
        <PathDialog category={category} start={subpath ? `${subpath}/` : ''}
                    onCancel={() => setEditing(false)}
                    onSave={v => { setEditing(false); onChange(v) }} />
      )}
    </div>
  )
}

export function PathDialog({ category, start, onCancel, onSave }: {
  category: string
  start: string
  onCancel: () => void
  onSave: (subpath: string) => void
}) {
  const [value, setValue] = useState(start)
  const [dirs, setDirs] = useState<string[]>([])
  const [exists, setExists] = useState(true)

  // Existing subfolders of what has been typed so far (the part up to the last "/").
  const parent = value.includes('/') ? value.slice(0, value.lastIndexOf('/')) : ''
  const lastPart = value.slice(value.lastIndexOf('/') + 1)
  useEffect(() => {
    let alive = true
    api.subfolders(category, parent).then(r => { if (alive) { setDirs(r.dirs); setExists(r.exists !== false) } })
      .catch(() => { if (alive) { setDirs([]); setExists(false) } })
    return () => { alive = false }
  }, [category, parent])

  const matches = dirs.filter(d => d.toLowerCase().startsWith(lastPart.toLowerCase()))
  const willCreate = !exists || (lastPart !== '' && !dirs.includes(lastPart))
  const location = parent ? `${category}/${parent}` : category
  const goTo = (path: string) => setValue(path ? `${path}/` : '')
  const up = parent.includes('/') ? parent.slice(0, parent.lastIndexOf('/')) : ''

  return (
    <div className="path-dialog" onKeyDown={e => { if (e.key === 'Escape') onCancel() }}>
      <div className="row-form">
        <span className="muted">{category} /</span>
        <input autoFocus value={value} onChange={e => setValue(e.target.value)} placeholder="subfolder/inside (empty = category folder)"
               onKeyDown={e => { if (e.key === 'Enter') onSave(value.trim()) }} aria-label="Folder inside the category" />
        <button onClick={onCancel}>Cancel</button>
        <button className="primary" onClick={() => onSave(value.trim())}>Use</button>
      </div>
      {willCreate && value.trim() && <p className="small warn-text">Folders that don't exist yet will be created when you apply.</p>}
      <div className="shortcuts">
        <span className="small muted">{exists ? `In ${location}:` : `${location} doesn't exist yet`}</span>
        {parent !== '' && <button className="chip" onClick={() => goTo(up)} title="One level up"><Icon name="undo" size={14} />up</button>}
        {matches.slice(0, 40).map(d => (
          <button key={d} className="chip" onClick={() => goTo((parent ? `${parent}/` : '') + d)}><Icon name="folder" size={14} />{d}</button>
        ))}
        {exists && dirs.length === 0 && <span className="small muted">no subfolders</span>}
        {exists && dirs.length > 0 && matches.length === 0 && <span className="small muted">no folder starts with “{lastPart}”</span>}
      </div>
    </div>
  )
}
