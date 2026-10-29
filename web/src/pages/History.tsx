import { useCallback, useEffect, useState } from 'react'
import { api, humanSize, shortDate, type Batch, type Scan, type Status, type TrashItem } from '../api'
import { Icon } from '../components/Icon'
import { Modal } from '../components/Modal'
import { PageHead } from '../components/PageHead'
import { QualityCard } from '../components/QualityCard'
import { ShowMore } from '../components/ShowMore'
import { STATUS_LABEL } from '../labels'

type Tab = 'scans' | 'applied' | 'trash'
const STATUS_ORDER: Status[] = ['moved', 'accepted', 'suggested', 'needs_review', 'rejected']

/** A thin bar showing how a scan's files are split over the statuses. */
function StatusBar({ scan }: { scan: Scan }) {
  const counts = scan.counts ?? {}
  const total = Object.values(counts).reduce((a, b) => a + (b ?? 0), 0) || 1
  return (
    <div className="status-bar" title={STATUS_ORDER.filter(s => counts[s]).map(s => `${counts[s]} ${STATUS_LABEL[s].toLowerCase()}`).join(' · ')}>
      {STATUS_ORDER.map(s => counts[s] ? <span key={s} className={`seg-${s}`} style={{ flex: counts[s]! / total }} /> : null)}
    </div>
  )
}

export function HistoryPage({ openScan, notify, fail }: {
  openScan: (id: number) => void
  notify: (m: string) => void
  fail: (e: unknown) => void
}) {
  const [scans, setScans] = useState<Scan[]>([])
  const [batches, setBatches] = useState<Batch[]>([])
  const [trash, setTrash] = useState<TrashItem[]>([])
  const [confirmEmpty, setConfirmEmpty] = useState(false)
  const [tab, setTab] = useState<Tab>('scans')

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
  const applied = batches.reduce((n, b) => n + b.total, 0)

  return (
    <div className="page">
      <PageHead title="History & trash" lead="How well suggestions matched your decisions, and everything that was scanned, applied or trashed." />

      <div className="stats">
        <div className="stat"><span className="stat-value">{scans.length}</span><span className="stat-label">scans</span></div>
        <div className="stat"><span className="stat-value">{applied}</span><span className="stat-label">files applied</span></div>
        <div className="stat"><span className="stat-value">{trash.length}</span><span className="stat-label">in trash · {humanSize(trashSize)}</span></div>
      </div>

      <QualityCard fail={fail} />

      <section className="card">
        <div className="section-head">
          <div className="seg" role="tablist">
            {([['scans', `Scans`, scans.length], ['applied', 'Applied', batches.length], ['trash', 'Trash', trash.length]] as const).map(([k, label, n]) => (
              <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'active' : ''} onClick={() => setTab(k)}>
                {label}<span className="count">{n}</span>
              </button>
            ))}
          </div>
          {tab === 'trash' && (
            <button className="danger" disabled={!trash.length} onClick={() => setConfirmEmpty(true)}>
              <Icon name="trash" size={16} />Empty trash…
            </button>
          )}
        </div>

        {tab === 'scans' && (
          <ShowMore items={scans} empty={<p className="muted small">No scans yet.</p>} className="simple-list entries" render={s => (
            <li key={s.id}>
              <button className="entry-main linkish" onClick={() => openScan(s.id)}>
                <span className="entry-title">Scan #{s.id}</span>
                <span className="entry-sub" title={s.inbox}>{shortDate(s.created_at)} · {s.inbox.split('/').filter(Boolean).pop()}</span>
              </button>
              <div className="entry-side">
                <StatusBar scan={s} />
                <span className="small muted">{s.total} files{s.status !== 'done' && <span className="warn-text"> · {s.status}</span>}</span>
              </div>
              <Icon name="chevron" size={16} className="muted entry-chev" />
            </li>
          )} />
        )}

        {tab === 'applied' && (
          <ShowMore items={batches} empty={<p className="muted small">Nothing applied yet.</p>} className="simple-list entries" render={b => (
            <li key={b.batch_id}>
              <div className="entry-main">
                <span className="entry-title">
                  {[b.moved && `${b.moved} moved`, b.copied && `${b.copied} copied`, b.trashed && `${b.trashed} to trash`]
                    .filter(Boolean).join(', ') || `${b.total} files`}
                </span>
                <span className="entry-sub">
                  {shortDate(b.moved_at)}
                  {b.undoable < b.total && ` · ${b.total - b.undoable} undone or emptied`}
                </span>
              </div>
              <button disabled={!b.undoable} onClick={() => undo(b.batch_id)}><Icon name="undo" size={15} />Undo</button>
            </li>
          )} />
        )}

        {tab === 'trash' && (
          <>
            <p className="muted small">
              Deleted files wait in a hidden <code>.classifiles-trash</code> folder in their inbox until you empty the trash;
              until then Undo brings them back.
            </p>
            <ShowMore items={trash.slice(0, 500)} limit={8} empty={<p className="muted small">The trash is empty.</p>}
                      className="simple-list entries" render={t => (
              <li key={t.id}>
                <div className="entry-main">
                  <span className="entry-title mono">{t.src.split('/').pop()}</span>
                  <span className="entry-sub">{shortDate(t.moved_at)}</span>
                </div>
                <span className="small muted">{humanSize(t.size)}</span>
              </li>
            )} />
          </>
        )}
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
