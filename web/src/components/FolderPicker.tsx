import { useEffect, useState } from 'react'
import { api, type FsListing } from '../api'
import { Modal } from './Modal'
import { Icon } from './Icon'

/** Browse the folders the server can see (WSL paths; Windows drives are under /mnt/<letter>). */
export function FolderPicker({ title, start, onPick, onClose }: {
  title: string
  start?: string
  onPick: (path: string) => void
  onClose: () => void
}) {
  const [listing, setListing] = useState<FsListing | null>(null)
  const [typed, setTyped] = useState(start ?? '')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const go = async (path?: string) => {
    setLoading(true)
    setError(null)
    try {
      const l = await api.browse(path)
      setListing(l)
      setTyped(l.path ?? '')
    } catch (e) {
      setError((e as Error).message)
      if (!listing) setListing(await api.browse())
    } finally { setLoading(false) }
  }

  useEffect(() => { go(start || undefined) }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <Modal title={title} onClose={onClose}>
      <p className="muted small">
        Windows drives are under <code>/mnt/c</code>, <code>/mnt/d</code>, … The folder you pick is used as shown.
      </p>
      <form className="row-form" onSubmit={e => { e.preventDefault(); go(typed) }}>
        <input value={typed} onChange={e => setTyped(e.target.value)} placeholder="/mnt/d/…" aria-label="Folder path" />
        <button type="submit">Go</button>
      </form>
      <div className="shortcuts">
        {listing?.shortcuts.map(s => <button key={s} className="chip" onClick={() => go(s)}>{s}</button>)}
      </div>
      {error && <div className="banner error">{error}</div>}
      <div className="picker-list" aria-busy={loading}>
        {listing?.parent && <button className="picker-item" onClick={() => go(listing.parent!)}><Icon name="undo" size={15} />..</button>}
        {listing?.dirs.map(d => (
          <button key={d} className="picker-item" onClick={() => go(`${listing.path}/${d}`.replace('//', '/'))}>
            <Icon name="folder" size={15} />{d}
          </button>
        ))}
        {listing?.path && listing.dirs.length === 0 && <p className="muted small">No subfolders.</p>}
        {!listing?.path && <p className="muted small">Pick a starting point above.</p>}
      </div>
      <div className="modal-actions">
        <span className="mono muted grow">{listing?.path ?? ''}</span>
        <button onClick={onClose}>Cancel</button>
        <button className="primary" disabled={!listing?.path} onClick={() => listing?.path && onPick(listing.path)}>
          Use this folder
        </button>
      </div>
    </Modal>
  )
}

/** A path input with a "Browse…" button. */
export function PathInput({ value, onChange, placeholder, label }: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
  label: string
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="path-input">
      <input value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} aria-label={label} />
      <button type="button" onClick={() => setOpen(true)}>Browse…</button>
      {open && (
        <FolderPicker title={label} start={value || undefined} onClose={() => setOpen(false)}
                      onPick={p => { onChange(p); setOpen(false) }} />
      )}
    </div>
  )
}
