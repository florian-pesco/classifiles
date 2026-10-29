import { useMemo } from 'react'
import type { PlannedMove } from '../api'
import { Icon } from './Icon'
import { Modal } from './Modal'
import { ShowMore } from './ShowMore'

const base = (p: string) => p.split('/').pop() ?? p

/** What "Apply" will do, grouped by where the files go, with problems first. */
export function ApplyDialog({ plan, busy, onCancel, onApply }: {
  plan: PlannedMove[]
  busy: boolean
  onCancel: () => void
  onApply: () => void
}) {
  const ok = plan.filter(m => !m.error)
  const problems = plan.filter(m => m.error)
  const count = (f: (m: PlannedMove) => boolean) => ok.filter(f).length
  const newFolders = new Set(ok.map(m => m.creates).filter(Boolean)).size

  const groups = useMemo(() => {
    const g = new Map<string, PlannedMove[]>()
    for (const m of ok) {
      const key = m.action === 'delete' ? 'Trash' : m.category
      g.set(key, [...(g.get(key) ?? []), m])
    }
    return [...g.entries()].sort(([a], [b]) => (a === 'Trash' ? 1 : b === 'Trash' ? -1 : a.localeCompare(b)))
  }, [ok])

  const summary: [number, string, string][] = [
    [count(m => m.action === 'move'), 'to move', 'act-move'],
    [count(m => m.action === 'copy'), 'to copy', 'act-copy'],
    [count(m => m.action === 'delete'), 'to trash', 'act-delete'],
    [count(m => m.renamed), 'renamed', 'new'],
    [newFolders, newFolders === 1 ? 'new folder' : 'new folders', 'new'],
  ]

  return (
    <Modal title={ok.length ? `Apply ${ok.length} change${ok.length === 1 ? '' : 's'}?` : 'Nothing to apply'} onClose={onCancel} wide>
      <div className="apply-summary">
        {summary.filter(([n]) => n > 0).map(([n, label, cls]) => (
          <span key={label} className={`badge ${cls}`}>{n} {label}</span>
        ))}
        {problems.length > 0 && <span className="badge act-none">{problems.length} can't be applied</span>}
      </div>

      {problems.length > 0 && (
        <section className="apply-problems">
          <h3><Icon name="alert" size={16} />Not applied</h3>
          <ul>
            {problems.map(m => (
              <li key={m.proposal_id}><span className="mono">{base(m.src)}</span><span className="small">{m.error}</span></li>
            ))}
          </ul>
        </section>
      )}

      <div className="apply-groups">
        {groups.map(([group, items]) => (
          <section key={group} className="apply-group">
            <h3>
              <Icon name={group === 'Trash' ? 'trash' : 'folder'} size={16} />
              {group}<span className="count">{items.length}</span>
            </h3>
            <ShowMore items={items} limit={6} className="apply-list" render={m => {
              const dstDir = m.dst ? m.dst.split('/').slice(0, -1).join('/') : ''
              const where = m.action === 'delete' ? 'hidden trash folder in the inbox' : [m.category, ...(m.subpath ? m.subpath.split('/') : [])].join(' › ')
              return (
                <li key={m.proposal_id}>
                  <div className="apply-file">
                    <span className="mono" title={m.src}>{base(m.src)}</span>
                    {m.renamed && m.dst && (
                      <span className="apply-rename"><Icon name="chevron" size={13} /><span className="mono">{base(m.dst)}</span></span>
                    )}
                  </div>
                  <div className="apply-where small" title={dstDir}>
                    {m.action === 'copy' && <span className="badge act-copy plain">copy</span>}
                    <span>{where}</span>
                    {m.creates && <span className="badge new plain" title={m.creates}>new folder</span>}
                  </div>
                </li>
              )
            }} />
          </section>
        ))}
      </div>

      <div className="modal-actions">
        <span className="muted small grow">You can undo this afterwards ("Undo last apply" or History).</span>
        <button onClick={onCancel}>Cancel</button>
        <button className="primary" onClick={onApply} disabled={busy || !ok.length}>
          <Icon name="check" size={16} />{busy ? 'Applying…' : `Apply ${ok.length} file${ok.length === 1 ? '' : 's'}`}
        </button>
      </div>
    </Modal>
  )
}
