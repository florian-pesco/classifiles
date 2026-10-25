import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  api, humanSize, shortDate,
  type Category, type PlannedMove, type Proposal, type Scan, type Settings, type Status,
} from '../api'
import { CategorySelect } from '../components/CategorySelect'
import { PathEditor } from '../components/PathEditor'
import { PathInput } from '../components/FolderPicker'
import { Modal } from '../components/Modal'
import { Preview } from '../components/Preview'
import { STATUS_LABEL } from '../labels'

const APPLIED_LABEL = { move: 'Moved', copy: 'Copied', delete: 'In trash' } as const
type Filter = Status | 'all' | 'duplicates'
const FILTERS: (Status | 'all')[] = ['all', 'needs_review', 'suggested', 'accepted', 'rejected', 'moved']

type SortKey = 'confidence' | 'confidence_asc' | 'name' | 'category' | 'kind' | 'size' | 'modified'
const SORTS: { key: SortKey; label: string }[] = [
  { key: 'confidence', label: 'Confidence ↓' },
  { key: 'confidence_asc', label: 'Confidence ↑' },
  { key: 'name', label: 'Name' },
  { key: 'category', label: 'Category' },
  { key: 'kind', label: 'File type' },
  { key: 'size', label: 'Size' },
  { key: 'modified', label: 'Modified (newest)' },
]

function sortRows(rows: Proposal[], key: SortKey) {
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

export function ReviewPage({ settings, categories, scanId, setScanId, notify, fail }: {
  settings: Settings
  categories: Category[]
  scanId: number | null
  setScanId: (id: number | null) => void
  notify: (m: string) => void
  fail: (e: unknown) => void
}) {
  const [scans, setScans] = useState<Scan[]>([])
  const [scan, setScan] = useState<Scan | null>(null)
  const [proposals, setProposals] = useState<Proposal[]>([])
  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortKey>('confidence')
  const [inbox, setInbox] = useState(settings.inboxes[0] ?? '')
  const [maxFiles, setMaxFiles] = useState(settings.max_files ? String(settings.max_files) : '')
  const [onlyNew, setOnlyNew] = useState(false)
  const [plan, setPlan] = useState<PlannedMove[] | null>(null)
  const [preview, setPreview] = useState<Proposal | null>(null)
  const [busy, setBusy] = useState(false)

  const loadScans = useCallback(async () => {
    const s = await api.scans()
    setScans(s)
    return s
  }, [])

  // Initial: list scans and open the latest unless a scan was chosen (e.g. from History).
  useEffect(() => {
    loadScans().then(s => { if (scanId == null && s[0]) setScanId(s[0].id) }).catch(fail)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Load the chosen scan + its proposals.
  useEffect(() => {
    if (scanId == null) { setScan(null); setProposals([]); return }
    Promise.all([api.scan(scanId), api.proposals(scanId)])
      .then(([s, rows]) => { setScan(s); setProposals(rows) })
      .catch(fail)
  }, [scanId, fail])

  // Poll while running.
  useEffect(() => {
    if (!scan || scan.status !== 'running') return
    const t = setInterval(async () => {
      try {
        const s = await api.scan(scan.id)
        setScan(s)
        if (s.status !== 'running') {
          clearInterval(t)
          setProposals(await api.proposals(s.id))
          loadScans()
        }
      } catch (e) { fail(e) }
    }, 700)
    return () => clearInterval(t)
  }, [scan, fail, loadScans])

  const counts = useMemo(() => {
    const c: Record<string, number> = { all: proposals.length, duplicates: 0 }
    for (const p of proposals) {
      c[p.status] = (c[p.status] ?? 0) + 1
      if (p.duplicate_of) c.duplicates += 1
    }
    return c
  }, [proposals])

  const visible = useMemo(() => {
    const needle = search.trim().toLowerCase()
    const rows = proposals.filter(p =>
      (filter === 'all' || (filter === 'duplicates' ? !!p.duplicate_of : p.status === filter)) &&
      (!needle || p.name.toLowerCase().includes(needle) || (p.final_target ?? '').toLowerCase().includes(needle)))
    return sortRows(rows, sort)
  }, [proposals, filter, search, sort])

  const startScan = async () => {
    try {
      const limit = parseInt(maxFiles, 10)
      const s = await api.startScan(inbox || undefined, limit > 0 ? limit : undefined, onlyNew)
      setProposals([])
      setScan(s)
      setScanId(s.id)
      loadScans()
    } catch (e) { fail(e) }
  }

  // Optimistic review: update the row immediately, roll back if the server refuses.
  const review = async (p: Proposal, action: 'accept' | 'reject' | 'reset', folder?: string, subpath?: string) => {
    const optimistic: Proposal = {
      ...p,
      status: action === 'accept' ? 'accepted' : action === 'reject' ? 'rejected'
        : (p.target && p.confidence >= (scan?.threshold ?? settings.threshold) ? 'suggested' : 'needs_review'),
      final_target: action === 'accept' ? (folder ?? p.final_target) : action === 'reset' ? p.target : p.final_target,
      subpath: subpath !== undefined ? subpath : action === 'reset' ? p.suggested_subpath : p.subpath,
    }
    setProposals(ps => ps.map(x => (x.id === p.id ? optimistic : x)))
    try {
      const saved = await api.review(p.id, action, folder, subpath)
      setProposals(ps => ps.map(x => (x.id === p.id ? saved : x)))
    } catch (e) {
      setProposals(ps => ps.map(x => (x.id === p.id ? p : x)))
      fail(e)
    }
  }

  const acceptSuggested = async (topLevel = false) => {
    if (!scan) return
    try {
      const { accepted } = await api.acceptSuggested(scan.id, topLevel)
      setProposals(await api.proposals(scan.id))
      notify(`Accepted ${accepted} suggested files${topLevel ? ' (category folders only, no subfolders)' : ''}.`)
    } catch (e) { fail(e) }
  }

  const openPlan = async () => {
    if (!scan) return
    try { setPlan(await api.planApply(scan.id)) } catch (e) { fail(e) }
  }

  const confirmApply = async () => {
    if (!scan) return
    setBusy(true)
    try {
      const res = await api.apply(scan.id)
      setPlan(null)
      const n = (a: string) => res.moved.filter(m => m.action === a).length
      const parts = [n('move') && `${n('move')} moved`, n('copy') && `${n('copy')} copied`,
                     n('delete') && `${n('delete')} to trash`].filter(Boolean)
      notify(`${parts.join(', ') || 'Nothing applied'}${res.errors.length ? `, ${res.errors.length} failed` : ''}.`)
      setProposals(await api.proposals(scan.id))
      loadScans()
    } catch (e) { fail(e) } finally { setBusy(false) }
  }

  const undoLast = async () => {
    try {
      const res = await api.undo()
      notify(res.batch_id ? `Undid ${res.restored.length} files${res.errors.length ? `, ${res.errors.length} could not be undone` : ''}.`
        : 'Nothing to undo.')
      if (scan) setProposals(await api.proposals(scan.id))
    } catch (e) { fail(e) }
  }

  const threshold = scan?.threshold ?? settings.threshold
  const running = scan?.status === 'running'

  return (
    <>
      <section className="card scan-card">
        <div className="scanbar">
          <PathInput value={inbox} onChange={setInbox} label="Inbox folder" placeholder="Inbox folder to scan" />
          <input className="limit" type="number" min={1} value={maxFiles} placeholder="all"
                 onChange={e => setMaxFiles(e.target.value)} aria-label="Maximum number of files"
                 title="Classify at most this many files (newest first). Empty = all." />
          <label className="check" title="Skip files already classified in an earlier scan">
            <input type="checkbox" checked={onlyNew} onChange={e => setOnlyNew(e.target.checked)} /> Only new
          </label>
          <button className="primary" onClick={startScan} disabled={running}>
            {running ? 'Scanning…' : 'Scan & classify'}
          </button>
        </div>
        {running && scan?.phase === 'second_opinion' && (
          <div className="row-form so-progress">
            <div className="progress grow" role="progressbar" aria-valuenow={scan.llm_done} aria-valuemax={scan.llm_total}>
              <div style={{ width: `${scan.llm_total ? (100 * scan.llm_done) / scan.llm_total : 0}%` }} />
              <span>
                {scan.llm_done === 0 ? 'Starting the language model (first time: one-time download)…'
                  : `Second opinion on unsure files: ${scan.llm_done} / ${scan.llm_total}`}
              </span>
            </div>
            <button onClick={() => api.cancelSecondOpinion().catch(fail)}>Skip</button>
          </div>
        )}
        {scan?.llm_error && <div className="banner error">Second opinion stopped: {scan.llm_error}</div>}
        {running && scan && scan.phase !== 'second_opinion' && (
          <div className="progress" role="progressbar" aria-valuenow={scan.processed} aria-valuemax={scan.total}>
            <div style={{ width: `${scan.total ? (100 * scan.processed) / scan.total : 0}%` }} />
            <span>{scan.processed} / {scan.total} files
              {scan.total < scan.inbox_total ? ` (of ${scan.inbox_total} in inbox)` : ''}</span>
          </div>
        )}
        {scan?.status === 'failed' && <div className="banner error">Scan failed: {scan.error}</div>}
      </section>

      <div className="toolbar">
        <select value={scanId ?? ''} onChange={e => setScanId(e.target.value ? Number(e.target.value) : null)}
                aria-label="Scan">
          {scans.length === 0 && <option value="">No scans yet</option>}
          {scans.map(s => (
            <option key={s.id} value={s.id}>
              Scan #{s.id} · {shortDate(s.created_at)} · {s.inbox.split('/').pop()} · {s.total} files
            </option>
          ))}
        </select>
        <input className="search" type="search" placeholder="Search name or category…" value={search}
               onChange={e => setSearch(e.target.value)} aria-label="Search" />
        <select value={sort} onChange={e => setSort(e.target.value as SortKey)} aria-label="Sort">
          {SORTS.map(s => <option key={s.key} value={s.key}>Sort: {s.label}</option>)}
        </select>
      </div>

      <nav className="filters">
        {FILTERS.map(f => (
          <button key={f} className={f === filter ? 'chip active' : 'chip'} onClick={() => setFilter(f)}>
            {f === 'all' ? 'All' : STATUS_LABEL[f]} <span className="count">{counts[f] ?? 0}</span>
          </button>
        ))}
        {counts.duplicates > 0 && (
          <button className={filter === 'duplicates' ? 'chip active' : 'chip dup-chip'} onClick={() => setFilter('duplicates')}>
            Duplicates <span className="count">{counts.duplicates}</span>
          </button>
        )}
        <span className="grow" />
        {scan && !running && scan.total < scan.inbox_total && (
          <span className="muted small">{scan.total} of {scan.inbox_total} inbox files ·</span>
        )}
        <span className="muted small">threshold {threshold.toFixed(2)}</span>
      </nav>

      {proposals.length === 0 && !running && (
        <p className="empty">No proposals yet. Pick an inbox folder and press <b>Scan &amp; classify</b>.</p>
      )}
      {proposals.length > 0 && visible.length === 0 && <p className="empty">Nothing matches.</p>}

      <ul className="list">
        {visible.map(p => (
          <ProposalRow key={p.id} p={p} categories={categories} threshold={threshold}
                       onReview={review} onPreview={() => setPreview(p)} />
        ))}
      </ul>

      <footer className="actions">
        <button onClick={() => acceptSuggested()} disabled={!counts.suggested}>
          Accept all suggested ({counts.suggested ?? 0})
        </button>
        <button onClick={() => acceptSuggested(true)} disabled={!counts.suggested}
                title="Accept the suggested categories, but put the files directly into the category folders">
          … category only
        </button>
        <button className="primary" onClick={openPlan} disabled={!counts.accepted}>
          Apply accepted ({counts.accepted ?? 0})
        </button>
        <span className="grow" />
        <button onClick={undoLast}>Undo last apply</button>
      </footer>

      {preview && <Preview p={preview} onClose={() => setPreview(null)} onError={m => fail(new Error(m))} />}

      {plan && (
        <Modal title="Apply these changes?" onClose={() => setPlan(null)} wide>
          <ul className="plan">
            {plan.map(m => (
              <li key={m.proposal_id} className={m.error ? 'bad' : ''}>
                <span>
                  <span className={`badge act-${m.action ?? 'none'}`}>
                    {m.action === 'delete' ? 'to trash' : m.action ?? 'error'}
                  </span>{' '}
                  <span className="mono">{m.src.split('/').pop()}</span>
                </span>
                <span className="muted mono small">{m.error ?? `→ ${m.dst}`}</span>
                {m.creates && <span className="small warn-text">creates new folder <span className="mono">{m.creates}</span></span>}
              </li>
            ))}
          </ul>
          <div className="modal-actions">
            <button onClick={() => setPlan(null)}>Cancel</button>
            <button className="primary" onClick={confirmApply} disabled={busy || plan.every(m => m.error)}>
              Apply {plan.filter(m => !m.error).length} files
            </button>
          </div>
        </Modal>
      )}
    </>
  )
}

function ProposalRow({ p, categories, threshold, onReview, onPreview }: {
  p: Proposal
  categories: Category[]
  threshold: number
  onReview: (p: Proposal, action: 'accept' | 'reject' | 'reset', folder?: string, subpath?: string) => void
  onPreview: () => void
}) {
  const cat = categories.find(c => c.name === p.final_target)
  const [open, setOpen] = useState(false)
  const done = p.status === 'moved'
  const confClass = p.confidence >= threshold ? 'high' : p.confidence >= threshold / 2 ? 'mid' : 'low'
  const looks = p.evidence.meta?.looks_like as string | undefined
  const source = p.evidence.meta?.source as string | undefined
  const viaOcr = String(p.evidence.meta?.note ?? '').startsWith('text read by OCR')
  const llmAgrees = !!p.llm_choice && p.llm_choice === p.final_target
  const statusLabel = done && p.applied_action ? APPLIED_LABEL[p.applied_action] : STATUS_LABEL[p.status]

  return (
    <li className={`row status-${p.status}`}>
      <div className="file">
        <button className="linkish name" onClick={() => setOpen(o => !o)} aria-expanded={open}>{p.name}</button>
        <div className="muted small">
          {p.kind}{looks ? ` · ${looks}` : ''} · {humanSize(p.size)} · {p.evidence.modified?.slice(0, 10)}
          {source && <> · from <span title="Where it was downloaded from">{source}</span></>}
          {viaOcr && <> · <span title="Text read by OCR (scanned PDF)">OCR</span></>}
          {' · '}<button className="linkish" onClick={onPreview}>preview</button>
        </div>
        {p.duplicate_of && (
          <div className="duplicate small">
            <span className="badge dup" title="Byte-for-byte identical content">
              {p.duplicate_kind === 'sorted' ? 'already in your folders' : 'duplicate in inbox'}
            </span>
            <span className="mono">{p.duplicate_of}</span>
            <button className="linkish" onClick={() => api.openFile(p.id, true, true).catch(() => undefined)}>show it</button>
          </div>
        )}
        {p.llm_choice && (
          <div className="second-opinion small">
            {llmAgrees
              ? <span className="badge so-agree" title="The language model chose the same category">✓ both agree</span>
              : (
                <>
                  <span className="badge so-differ" title="The language model chose a different category">2nd opinion: {p.llm_choice}</span>
                  {!done && (
                    <button className="linkish" onClick={() => onReview(p, 'accept', p.llm_choice!)}>use it</button>
                  )}
                </>
              )}
          </div>
        )}
        {open && (
          <div className="details small">
            {p.reason && <div><b>Why:</b> {p.reason}</div>}
            {p.alternatives.length > 0 && (
              <div><b>Scores:</b> {p.alternatives.map(a => `${a.folder} ${(a.probability * 100).toFixed(0)}%`).join(' · ')}</div>
            )}
            {done && p.applied_dst && <div><b>Now at:</b> <span className="mono">{p.applied_dst}</span></div>}
            {p.evidence.text
              ? <div className="snippet">{p.evidence.text.slice(0, 400)}</div>
              : <div className="warn-text">No readable content: judged by file name and type only.</div>}
          </div>
        )}
      </div>

      <div className="conf" title={`${p.source}: ${(p.confidence * 100).toFixed(0)}%`}>
        <div className="bar"><div className={confClass} style={{ width: `${p.confidence * 100}%` }} /></div>
        <span className="small">{(p.confidence * 100).toFixed(0)}%{p.source === 'rule' ? ' · rule' : ''}</span>
      </div>

      <CategorySelect value={p.final_target} categories={categories} disabled={done}
                      onChange={name => name && onReview(p, 'accept', name)} label={`Category for ${p.name}`} />

      <span className={`badge ${p.status}`}>{statusLabel}</span>

      {cat && cat.source !== 'delete' && (
        <PathEditor category={cat.name} subpath={p.subpath ?? ''} suggested={p.suggested_subpath ?? ''}
                    newFolder={p.new_folder} disabled={done}
                    onChange={sub => onReview(p, 'accept', p.final_target ?? undefined, sub)} />
      )}

      <div className="rowbtns">
        {!done && p.status !== 'accepted' && (
          <button className="icon ok" onClick={() => onReview(p, 'accept')} disabled={!p.final_target} title="Accept">✓</button>
        )}
        {!done && p.status !== 'rejected' && (
          <button className="icon no" onClick={() => onReview(p, 'reject')} title="Keep in inbox">✕</button>
        )}
        {!done && (p.status === 'accepted' || p.status === 'rejected') && (
          <button className="icon" onClick={() => onReview(p, 'reset')} title="Reset">↺</button>
        )}
      </div>
    </li>
  )
}
