import { useCallback, useEffect, useState } from 'react'
import { api, humanSize, shortDate, type DuplicateDetails, type Proposal } from '../api'
import { FileView } from './Preview'
import { Modal } from './Modal'

/**
 * Review duplicates one by one: both copies side by side, re-checked when opened, and the facts
 * behind the proposal. Keys: ← / → previous / next, D delete this copy, K keep both, Esc close.
 */
export function CompareDuplicate({ list, startId, onDecide, onClose }: {
  list: Proposal[]
  startId: number
  onDecide: (p: Proposal, decision: 'delete' | 'keep') => Promise<void>
  onClose: () => void
}) {
  const [index, setIndex] = useState(() => Math.max(0, list.findIndex(p => p.id === startId)))
  const p = list[index]
  const [details, setDetails] = useState<DuplicateDetails | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (!p) return
    let alive = true
    setDetails(null)
    setError(null)
    api.duplicateDetails(p.id).then(d => { if (alive) setDetails(d) }).catch(e => { if (alive) setError((e as Error).message) })
    return () => { alive = false }
  }, [p])

  const go = useCallback((delta: number) => setIndex(i => Math.min(list.length - 1, Math.max(0, i + delta))), [list.length])

  const decide = useCallback(async (decision: 'delete' | 'keep') => {
    if (!p || busy) return
    setBusy(true)
    try {
      await onDecide(p, decision)
      if (index < list.length - 1) go(1)
      else onClose()
    } finally { setBusy(false) }
  }, [p, busy, onDecide, index, list.length, go, onClose])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === 'ArrowRight') go(1)
      else if (e.key === 'ArrowLeft') go(-1)
      else if (e.key === 'd' || e.key === 'D') decide('delete')
      else if (e.key === 'k' || e.key === 'K') decide('keep')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [go, decide])

  if (!p) return null
  const decided = p.status === 'accepted' ? 'marked for deletion' : p.status === 'rejected' ? 'kept' : null

  return (
    <Modal title={`Duplicate ${index + 1} of ${list.length}`} onClose={onClose} wide>
      <div className="compare">
        <div className="compare-side">
          <div className="compare-head">
            <span className="badge dup">this copy · would be deleted</span>
            <div className="mono small">{p.name}</div>
          </div>
          <FileView url={api.fileUrl(p.id)} name={p.name} ext={p.evidence.ext} kind={p.kind} fallbackText={p.evidence.text} compact />
        </div>
        <div className="compare-side">
          <div className="compare-head">
            <span className="badge so-agree">{p.duplicate_kind === 'sorted' ? 'kept · already in your folders' : 'kept · in the inbox'}</span>
            <div className="mono small">{p.duplicate_of}</div>
          </div>
          <FileView url={`${api.fileUrl(p.id)}?duplicate=true`} name={p.duplicate_of ?? ''} ext={p.evidence.ext} kind={p.kind} compact />
        </div>
      </div>

      {error && <div className="banner error">{error}</div>}
      {!details && !error && <p className="muted small">Checking both files…</p>}
      {details && (
        <div className="facts">
          <div className={details.identical_now ? 'verdict ok' : 'verdict bad'}>
            {details.identical_now
              ? 'Verified just now: both files are byte-for-byte identical.'
              : details.other.exists ? 'Not identical anymore: one of the files changed. Better keep both.'
                : 'The other copy is gone. Better keep this one.'}
          </div>
          <table className="facts-table small">
            <thead><tr><th /><th>this copy</th><th>kept copy</th><th /></tr></thead>
            <tbody>
              <Row label="Size" a={details.this.size != null ? `${humanSize(details.this.size)} (${details.this.size.toLocaleString()} bytes)` : '—'}
                   b={details.other.size != null ? `${humanSize(details.other.size)} (${details.other.size.toLocaleString()} bytes)` : 'missing'}
                   same={details.this.size === details.other.size} />
              <Row label="Content fingerprint" a={details.this.fingerprint?.slice(0, 16) ?? '—'} b={details.other.fingerprint?.slice(0, 16) ?? '—'}
                   same={!!details.this.fingerprint && details.this.fingerprint === details.other.fingerprint} mono />
              <Row label="Modified" a={details.this.modified ? shortDate(details.this.modified) : '—'}
                   b={details.other.modified ? shortDate(details.other.modified) : '—'} same={null} />
              <Row label="Name" a={details.this.label} b={details.other.label.split('/').pop() ?? ''} same={null} />
            </tbody>
          </table>
          <p className="small muted">{details.keep_reason} {details.method}</p>
        </div>
      )}

      <div className="modal-actions">
        <span className="small muted grow">
          {decided ? `This one: ${decided}. ` : ''}Keys: ← → browse · D delete · K keep both · Esc close
        </span>
        <button onClick={() => go(-1)} disabled={index === 0}>←</button>
        <button onClick={() => go(1)} disabled={index === list.length - 1}>→</button>
        <button onClick={() => decide('keep')} disabled={busy}>Keep both (K)</button>
        <button className="danger" onClick={() => decide('delete')} disabled={busy || (details !== null && !details.identical_now)}>
          Delete this copy (D)
        </button>
      </div>
    </Modal>
  )
}

function Row({ label, a, b, same, mono = false }: { label: string; a: string; b: string; same: boolean | null; mono?: boolean }) {
  return (
    <tr>
      <th>{label}</th>
      <td className={mono ? 'mono' : undefined}>{a}</td>
      <td className={mono ? 'mono' : undefined}>{b}</td>
      <td className={same === null ? 'muted' : same ? 'ok-text' : 'warn-text'}>
        {same === null ? 'may differ' : same ? 'same' : 'differs'}
      </td>
    </tr>
  )
}
