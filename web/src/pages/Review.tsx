import { useEffect, useMemo, useState } from 'react'
import {
  api, shortDate,
  type Category, type PlannedMove, type Proposal, type ReviewAction, type ReviewBatch, type Settings,
} from '../api'
import { ApplyDialog } from '../components/ApplyDialog'
import { BatchPanel } from '../components/BatchPanel'
import { BulkBar } from '../components/BulkBar'
import { CompareDuplicate } from '../components/CompareDuplicate'
import { Icon } from '../components/Icon'
import { PageHead } from '../components/PageHead'
import { Preview } from '../components/Preview'
import { useMessages } from '../messages'
import { ProposalRow } from './review/ProposalRow'
import { ScanBar } from './review/ScanBar'
import { type Filter, SORTS, type SortKey } from './review/sort'
import { StatusFilters } from './review/StatusFilters'
import { useProposals } from './review/useProposals'
import { useScan } from './review/useScan'
import { useSelection } from './review/useSelection'

export function ReviewPage({ settings, categories, scanId, setScanId }: {
  settings: Settings
  categories: Category[]
  scanId: number | null
  setScanId: (id: number | null) => void
}) {
  const { notify, fail } = useMessages()
  const [filter, setFilter] = useState<Filter>('all')
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState<SortKey>('confidence')
  const [batch, setBatch] = useState<ReviewBatch | null>(null) // list filtered to this batch
  const [batches, setBatches] = useState<ReviewBatch[]>([])
  const [plan, setPlan] = useState<PlannedMove[] | null>(null)
  const [preview, setPreview] = useState<Proposal | null>(null)
  // Compare session: the duplicates to go through, fixed when the window opens (so decisions made
  // in it don't reshuffle the list), plus the one to start with.
  const [compare, setCompare] = useState<{ ids: number[]; start: number } | null>(null)
  const [busy, setBusy] = useState(false)

  const rows = useProposals({ scanId, filter, search, sort, batch })
  const { proposals, setProposals, loadRows, merge, counts, visible } = rows
  const { scans, scan, loadScans, startScan } = useScan(scanId, setScanId, rows)
  const sel = useSelection(proposals, visible)
  const { clear: clearSelection } = sel

  // Batch suggestions: refreshed whenever decisions change (debounced), not while scanning.
  const decisions = useMemo(() => proposals.map(p => `${p.id}:${p.status}:${p.final_target}`).join(','), [proposals])
  useEffect(() => {
    if (scanId == null || scan?.status === 'running' || !decisions) { setBatches([]); return }
    const t = setTimeout(() => api.reviewBatches(scanId).then(setBatches).catch(fail), 300)
    return () => clearTimeout(t)
  }, [scanId, scan?.status, decisions, fail])
  // A new scan starts without a batch filter or selection.
  useEffect(() => { setBatch(null); clearSelection() }, [scanId, clearSelection])

  const duplicates = useMemo(() => proposals.filter(p => p.duplicate_of && p.status !== 'moved')
    .sort((a, b) => a.name.localeCompare(b.name)), [proposals])
  // still to decide: not yet marked for deletion (accepted) or kept (rejected)
  const undecidedDuplicates = useMemo(
    () => duplicates.filter(p => p.status === 'suggested' || p.status === 'needs_review'), [duplicates])
  const openCompare = (startId?: number) => {
    const ids = undecidedDuplicates.map(p => p.id)
    if (startId !== undefined && !ids.includes(startId)) ids.unshift(startId) // opened on a decided row
    if (ids.length) setCompare({ ids, start: startId ?? ids[0] })
  }
  const compareList = useMemo(
    () => (compare ? compare.ids.map(id => proposals.find(p => p.id === id)).filter((p): p is Proposal => !!p) : []),
    [compare, proposals])

  const pickBatch = (b: ReviewBatch | null) => {
    setBatch(b)
    clearSelection()
    if (b) setFilter('all')
  }

  // Say how many other files followed a decision.
  const reportRelearned = (others: Proposal[]) => {
    if (!others.length) return
    const moved = others.filter(p => p.relearned).length
    notify(moved
      ? `Learned from your decision: ${moved} similar file${moved === 1 ? '' : 's'} updated (marked "updated").`
      : `${others.length} suggestion${others.length === 1 ? '' : 's'} went back to the model's own guess.`)
  }

  // Optimistic review: update the row immediately, roll back if the server refuses.
  const review = async (p: Proposal, action: ReviewAction, folder?: string, subpath?: string,
                        name?: string, useSuggestedName?: boolean) => {
    const optimistic: Proposal = {
      ...p,
      status: action === 'accept' ? 'accepted' : action === 'reject' ? 'rejected'
        : (p.target && p.confidence >= (scan?.threshold ?? settings.threshold) ? 'suggested' : 'needs_review'),
      final_target: action === 'accept' ? (folder ?? p.final_target) : action === 'reset' ? p.target : p.final_target,
      subpath: subpath !== undefined ? subpath : action === 'reset' ? p.suggested_subpath : p.subpath,
      new_name: useSuggestedName === true ? p.suggested_name : useSuggestedName === false ? null : p.new_name,
    }
    setProposals(ps => ps.map(x => (x.id === p.id ? optimistic : x)))
    try {
      const { relearned_others: others, ...saved } = await api.review(p.id, action, folder, subpath, name, useSuggestedName)
      merge([saved, ...others])
      reportRelearned(others)
    } catch (e) {
      setProposals(ps => ps.map(x => (x.id === p.id ? p : x)))
      fail(e)
    }
  }

  const bulk = async (action: ReviewAction, folder?: string, subpath?: string, useSuggestedName?: boolean) => {
    const ids = sel.selectedRows.filter(p => p.status !== 'moved').map(p => p.id)
    if (!ids.length) return
    setBusy(true)
    try {
      const res = await api.bulkReview(ids, action, folder, subpath, useSuggestedName)
      merge([...res.updated, ...res.relearned])
      const verb = useSuggestedName === true ? 'Suggested names for' : useSuggestedName === false ? 'Original names for'
        : action === 'accept' ? 'Accepted' : action === 'reject' ? 'Kept in inbox:' : 'Reset'
      notify(`${verb} ${res.updated.length} file${res.updated.length === 1 ? '' : 's'}`
        + (res.errors.length ? `; ${res.errors.length} failed (${res.errors[0].error})` : '')
        + (res.relearned.filter(p => p.relearned).length
          ? `; ${res.relearned.filter(p => p.relearned).length} similar files updated from it.` : '.'))
      clearSelection()
    } catch (e) { fail(e) } finally { setBusy(false) }
  }

  const acceptSuggested = async (topLevel = false) => {
    if (!scan) return
    try {
      const { accepted } = await api.acceptSuggested(scan.id, topLevel)
      loadRows(await api.proposals(scan.id))
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
      loadRows(await api.proposals(scan.id))
      loadScans()
    } catch (e) { fail(e) } finally { setBusy(false) }
  }

  const undoLast = async () => {
    try {
      const res = await api.undo()
      notify(res.batch_id ? `Undid ${res.restored.length} files${res.errors.length ? `, ${res.errors.length} could not be undone` : ''}.`
        : 'Nothing to undo.')
      if (scan) loadRows(await api.proposals(scan.id))
    } catch (e) { fail(e) }
  }

  const threshold = scan?.threshold ?? settings.threshold
  const running = scan?.status === 'running'

  const scanLead = scan
    ? <>Scan #{scan.id} · {shortDate(scan.created_at)} · {scan.total} files
        {scan.total < scan.inbox_total ? ` of ${scan.inbox_total} in the inbox` : ''} · threshold {threshold.toFixed(2)}</>
    : 'Scan an inbox folder: each file gets a category, a folder and a name to review.'

  return (
    <div className="page">
      <PageHead title="Review" lead={scanLead}>
        <select className="scan-select" value={scanId ?? ''} onChange={e => setScanId(e.target.value ? Number(e.target.value) : null)}
                aria-label="Scan">
          {scans.length === 0 && <option value="">No scans yet</option>}
          {scans.map(s => (
            <option key={s.id} value={s.id}>
              Scan #{s.id} · {shortDate(s.created_at)} · {s.inbox.split('/').pop()} · {s.total} files
            </option>
          ))}
        </select>
      </PageHead>

      <ScanBar settings={settings} scan={scan} hasScans={scans.length > 0} onStart={startScan} />

      <StatusFilters filter={filter} setFilter={setFilter} counts={counts} onResort={rows.resort}
                     undecidedDuplicates={undecidedDuplicates.length} onCompare={() => openCompare()} />

      <div className="toolbar">
        <label className="searchbox">
          <Icon name="search" size={16} />
          <input type="search" placeholder="Search name or category…" value={search}
                 onChange={e => setSearch(e.target.value)} aria-label="Search" />
        </label>
        <select value={sort} onChange={e => setSort(e.target.value as SortKey)} aria-label="Sort">
          {SORTS.map(s => <option key={s.key} value={s.key}>Sort: {s.label}</option>)}
        </select>
      </div>

      {!running && <BatchPanel batches={batches} active={batch?.id ?? null} onPick={pickBatch} />}

      {(batch || visible.length > 0) && (
        <div className="select-line small">
          <label className="check">
            <input type="checkbox" checked={sel.allVisibleSelected} onChange={sel.selectAllVisible} disabled={!visible.length} />
            Select all {visible.length} shown
          </label>
          {batch && (
            <span className="chip active batch-chip" title={batch.why}>
              Batch: {batch.title}
              <button className="linkish" onClick={() => pickBatch(null)} aria-label="Show all files"><Icon name="x" size={14} /></button>
            </span>
          )}
          <span className="muted grow">Shift+click a checkbox to select a range.</span>
        </div>
      )}

      {proposals.length === 0 && !running && (
        <div className="empty"><Icon name="inbox" size={30} />No proposals yet. Pick an inbox folder and press <b>Scan &amp; classify</b>.</div>
      )}
      {proposals.length > 0 && visible.length === 0 && <p className="empty">Nothing matches.</p>}

      <ul className="list">
        {visible.map(p => (
          <ProposalRow key={p.id} p={p} categories={categories} threshold={threshold}
                       selected={sel.selected.has(p.id)} onSelect={shift => sel.toggle(p.id, shift)}
                       onCompare={() => openCompare(p.id)}
                       onReview={review} onPreview={() => setPreview(p)} />
        ))}
      </ul>

      {sel.selectedRows.length > 0 ? (
        <footer className="actions bulk">
          <BulkBar selected={sel.selectedRows} categories={categories} onAction={bulk} onClear={clearSelection} />
        </footer>
      ) : (
        <footer className="actions">
          <button onClick={() => acceptSuggested()} disabled={!counts.suggested}>
            <Icon name="check" size={16} />Accept all suggested ({counts.suggested ?? 0})
          </button>
          <button className="hide-m" onClick={() => acceptSuggested(true)} disabled={!counts.suggested}
                  title="Accept the suggested categories, but put the files directly into the category folders">
            … category only
          </button>
          <span className="grow" />
          <button className="ghost" onClick={undoLast} title="Undo last apply"><Icon name="undo" size={16} /><span className="hide-m">Undo last apply</span></button>
          <button className="primary" onClick={openPlan} disabled={!counts.accepted}>
            Apply accepted ({counts.accepted ?? 0})<Icon name="chevron" size={16} />
          </button>
        </footer>
      )}

      {preview && <Preview p={preview} onClose={() => setPreview(null)} onError={m => fail(new Error(m))} />}
      {compare && compareList.length > 0 && (
        <CompareDuplicate list={compareList} startId={compare.start} onClose={() => setCompare(null)}
                          onDecide={(p, d) => (d === 'delete' ? review(p, 'accept', p.final_target ?? undefined) : review(p, 'reject'))} />
      )}

      {plan && <ApplyDialog plan={plan} busy={busy} onCancel={() => setPlan(null)} onApply={confirmApply} />}
    </div>
  )
}
