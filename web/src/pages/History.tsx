import { useCallback, useEffect, useState } from 'react'
import { api, humanSize, shortDate, type Batch, type Scan, type TrashItem } from '../api'
import { Modal } from '../components/Modal'
import { QualityCard } from '../components/QualityCard'
import { STATUS_LABEL } from '../labels'
import { PageHead } from '../components/PageHead'

export function HistoryPage({ openScan, notify, fail }: {
  openScan: (id: number) => void
  notify: (m: string) => void
  fail: (e: unknown) => void
}) {
  const [scans, setScans] = useState<Scan[]>([])
  const [batches, setBatches] = useState<Batch[]>([])
  const [trash, setTrash] = useState<TrashItem[]>([])
  const [confirmEmpty, setConfirmEmpty] = useState(false)

  const load = useCallback(() => {
    Promise.all([api.scans(), api.batches(), api.trash()])
      .then(([s, b, t]) => { setScans(s); setBatches(b); setTrash(t) })
      .catch(fail)
  }, [fail])
  useEffect(load, [load])

  const undo = async (batchId: string) => {
    try {
      const r = await api.undo(batchId)
      notify(`Undid ${r.restored.length} files${r.errors.length ? `; ${r.errors.length} skipped: ${r.errors[0].error}` : ''}.`)
      load()
    } catch (e) { fail(e) }
  }

  const empty = async () => {
    try {
      const r = await api.emptyTrash()
      setConfirmEmpty(false)
      notify(`Permanently deleted ${r.purged} files${r.errors.length ? `, ${r.errors.length} failed` : ''}.`)
      load()
    } catch (e) { fail(e) }
  }

  const trashSize = trash.reduce((n, t) => n + (t.size ?? 0), 0)

  return (
    <div className="page">
      <PageHead title="History & trash" lead="How well the suggestions matched your decisions, every scan, every applied change, and what is in the trash." />
      <QualityCard fail={fail} />

      <section className="card">
        <div className="section-head"><h3>Scans</h3></div>
        {scans.length === 0 && <p className="muted small">No scans yet.</p>}
        <ul className="simple-list">
          {scans.map(s => (
            <li key={s.id}>
              <button className="linkish" onClick={() => openScan(s.id)}>Scan #{s.id}</button>
              <span className="muted small">{shortDate(s.created_at)}</span>
              <span className="mono small grow">{s.inbox}</span>
              <span className="small">
                {s.total} files
                {s.status !== 'done' && <span className="warn-text"> · {s.status}</span>}
                {s.counts && Object.entries(s.counts).map(([k, v]) => ` · ${v} ${STATUS_LABEL[k as keyof typeof STATUS_LABEL]?.toLowerCase() ?? k}`)}
              </span>
            </li>
          ))}
        </ul>
      </section>

      <section className="card">
        <div className="section-head"><h3>Applied changes</h3></div>
        {batches.length === 0 && <p className="muted small">Nothing applied yet.</p>}
        <ul className="simple-list">
          {batches.map(b => (
            <li key={b.batch_id}>
              <span className="small">{shortDate(b.moved_at)}</span>
              <span className="small grow">
                {[b.moved && `${b.moved} moved`, b.copied && `${b.copied} copied`, b.trashed && `${b.trashed} to trash`]
                  .filter(Boolean).join(', ')}
                {b.undoable < b.total && <span className="muted"> · {b.total - b.undoable} undone/emptied</span>}
              </span>
              <button disabled={!b.undoable} onClick={() => undo(b.batch_id)}>Undo</button>
            </li>
          ))}
        </ul>
      </section>

      <section className="card">
        <div className="section-head">
          <h3>Trash</h3>
          <button className="danger" disabled={!trash.length} onClick={() => setConfirmEmpty(true)}>Empty trash…</button>
        </div>
        <p className="muted small">
          {trash.length ? `${trash.length} files, ${humanSize(trashSize)}. ` : 'Empty. '}
          Files you send to Delete wait here (in a hidden <code>.classifiles-trash</code> folder in their inbox)
          until you empty the trash, and can be restored with Undo.
        </p>
        <ul className="simple-list">
          {trash.slice(0, 200).map(t => (
            <li key={t.id}>
              <span className="mono small grow">{t.src.split('/').pop()}</span>
              <span className="muted small">{humanSize(t.size)} · {shortDate(t.moved_at)}</span>
            </li>
          ))}
        </ul>
      </section>

      {confirmEmpty && (
        <Modal title="Permanently delete?" onClose={() => setConfirmEmpty(false)}>
          <p>This deletes {trash.length} files ({humanSize(trashSize)}) for good. It cannot be undone.</p>
          <div className="modal-actions">
            <button onClick={() => setConfirmEmpty(false)}>Cancel</button>
            <button className="danger" onClick={empty}>Delete permanently</button>
          </div>
        </Modal>
      )}
    </div>
  )
}
