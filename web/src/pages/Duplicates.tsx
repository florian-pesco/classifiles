import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  api, humanSize, type DuplicateGroup, type DuplicateOverview, type DuplicateSearch, type FolderPair, type TrashResult,
} from '../api'
import { Icon } from '../components/Icon'
import { Modal } from '../components/Modal'
import { PageHead } from '../components/PageHead'
import { ShowMore } from '../components/ShowMore'
import { useMessages } from '../messages'

type Tab = 'files' | 'folders' | 'kept'
type Verdict = 'all' | 'clear' | 'look'
type Spread = 'any' | DuplicateGroup['spread']

const SPREAD_LABEL: Record<DuplicateGroup['spread'], string> = {
  folder: 'same folder', category: 'same category', categories: 'different categories',
}

/** A pending bulk action, confirmed in a dialog first. */
type Confirm = { title: string; text: string; run: () => Promise<TrashResult> }

const fileName = (relpath: string) => relpath.split('/').pop() ?? relpath
const dateOf = (mtime: number) => new Date(mtime * 1000).toISOString().slice(0, 10)

function duration(seconds: number | null | undefined) {
  if (seconds == null) return ''
  if (seconds < 90) return 'less than 2 min left'
  if (seconds < 3600) return `about ${Math.round(seconds / 60)} min left`
  return `about ${(seconds / 3600).toFixed(1)} h left`
}

function SearchProgress({ s }: { s: DuplicateSearch }) {
  const pct = s.bytes_total ? (100 * (s.bytes_done ?? 0)) / s.bytes_total : 0
  const text = s.phase === 'listing'
    ? (s.message ?? 'Listing files…')
    : `${s.phase === 'comparing' ? 'Comparing files of the same size' : 'Verifying identical files'} · `
      + `${(s.done ?? 0).toLocaleString()}/${(s.total ?? 0).toLocaleString()} · ${duration(s.eta_seconds)}`
  return (
    <div className="progress" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemax={100}>
      <div style={{ width: `${s.phase === 'listing' ? 0 : pct}%` }} />
      <span>{text}</span>
    </div>
  )
}

function GroupCard({ g, onTrash, onIgnore }: {
  g: DuplicateGroup
  onTrash: (keep: number[]) => void
  onIgnore: () => void
}) {
  const { fail } = useMessages()
  const [keep, setKeep] = useState<Set<number>>(new Set(g.keep != null ? [g.keep] : []))
  const toggle = (id: number) => setKeep(k => {
    const next = new Set(k)
    if (next.has(id)) next.delete(id); else next.add(id)
    return next
  })
  const trashing = g.copies.length - keep.size
  const title = (g.copies.find(c => c.id === g.keep) ?? g.copies[0]).relpath
  return (
    <li className="dup-group">
      <div className="dup-group-head">
        <Icon name="copy" size={16} className="muted" />
        <span className="mono-title grow" title={title}>{fileName(title)}</span>
        <span className="badge plain">{SPREAD_LABEL[g.spread]}</span>
        <span className={`badge ${g.clear ? 'suggested' : 'needs_review'}`}>{g.clear ? 'clear' : 'needs a look'}</span>
        <span className="small muted">{humanSize(g.size)} × {g.copies.length}</span>
      </div>
      <p className="small muted dup-reason">{g.reason}</p>
      <ul className="dup-copies">
        {g.copies.map(c => (
          <li key={c.id} className={keep.has(c.id) ? 'keep' : 'drop'}>
            <label className="check">
              <input type="checkbox" checked={keep.has(c.id)} onChange={() => toggle(c.id)} />
              <span className="dup-copy-label">
                <span className="mono" title={c.folder}>{c.label}</span>
                <span className="small muted">
                  {dateOf(c.mtime)}{c.id === g.keep && ' · suggested'}{keep.has(c.id) ? ' · keep' : ' · to trash'}
                </span>
              </span>
            </label>
            <span className="rowbtns">
              <button className="icon" title="Open" onClick={() => api.openCopy(c.id).catch(fail)}><Icon name="eye" size={15} /></button>
              <button className="icon" title="Show in Explorer" onClick={() => api.openCopy(c.id, true).catch(fail)}><Icon name="folder" size={15} /></button>
            </span>
          </li>
        ))}
      </ul>
      <div className="dup-actions">
        <button onClick={onIgnore} title="These copies are wanted: don't show them again">Keep all</button>
        <button className="primary" disabled={!keep.size || !trashing} onClick={() => onTrash([...keep])}>
          <Icon name="trash" size={15} />{keep.size ? `Trash ${trashing} other${trashing === 1 ? '' : 's'}` : 'Choose a copy to keep'}
        </button>
      </div>
    </li>
  )
}

function PairRow({ p, onKeep }: { p: FolderPair; onKeep: (keep: string, drop: string, dropLabel: string) => void }) {
  const contained = (n: number) => n > 0 && p.files >= n
  return (
    <li className="dup-pair">
      <div className="entry-main">
        <span className="entry-title">{p.files.toLocaleString()} identical file{p.files === 1 ? '' : 's'} · {humanSize(p.bytes)}</span>
        <span className="entry-sub mono" title={p.a}>A: {p.a_label} ({p.a_files} files{contained(p.a_files) ? ', all also in B' : ''})</span>
        <span className="entry-sub mono" title={p.b}>B: {p.b_label} ({p.b_files} files{contained(p.b_files) ? ', all also in A' : ''})</span>
      </div>
      <div className="rowbtns">
        <button onClick={() => onKeep(p.a, p.b, p.b_label)}>Keep A's copies</button>
        <button onClick={() => onKeep(p.b, p.a, p.a_label)}>Keep B's copies</button>
      </div>
    </li>
  )
}

export function DuplicatesPage({ roots }: { roots: string[] }) {
  const { notify, fail } = useMessages()
  const [root, setRoot] = useState<string>(roots[0] ?? '')
  const [data, setData] = useState<DuplicateOverview | null>(null)
  const [kept, setKept] = useState<DuplicateGroup[]>([])
  const [search, setSearch] = useState<DuplicateSearch>({ state: 'idle' })
  const [tab, setTab] = useState<Tab>('files')
  const [verdict, setVerdict] = useState<Verdict>('all')
  const [spread, setSpread] = useState<Spread>('any')
  const [confirm, setConfirm] = useState<Confirm | null>(null)
  const scope = root || undefined

  const load = useCallback(() => {
    Promise.all([api.duplicates(scope), api.ignoredDuplicates(scope)])
      .then(([d, k]) => { setData(d); setSearch(d.status); setKept(k) })
      .catch(fail)
  }, [scope, fail])
  useEffect(load, [load])

  // Follow a running search; reload the groups when it ends.
  useEffect(() => {
    if (search.state !== 'running') return
    const t = setInterval(() => {
      api.duplicateSearch().then(s => { setSearch(s); if (s.state !== 'running') load() }).catch(fail)
    }, 1000)
    return () => clearInterval(t)
  }, [search.state, load, fail])

  const report = (r: TrashResult) => {
    notify(`Moved ${r.trashed} cop${r.trashed === 1 ? 'y' : 'ies'} (${humanSize(r.bytes)}) to the trash. `
      + `Undo in History & trash.${r.errors.length ? ` ${r.errors.length} skipped: ${r.errors[0].error}` : ''}`)
    load()
  }
  const act = (run: () => Promise<TrashResult>) => run().then(report).catch(fail)

  const startSearch = () => api.searchDuplicates(scope).then(setSearch).catch(fail)
  const ignore = (hashes: string[], ignored = true) =>
    api.ignoreDuplicates(hashes, ignored).then(() => { notify(ignored ? 'Kept as they are.' : 'Shown again.'); load() }).catch(fail)

  const groups = useMemo(() => (data?.groups ?? []).filter(g =>
    (verdict === 'all' || (verdict === 'clear') === g.clear) && (spread === 'any' || g.spread === spread)), [data, verdict, spread])
  const clear = (data?.groups ?? []).filter(g => g.clear)
  const clearBytes = clear.reduce((n, g) => n + g.wasted, 0)
  const running = search.state === 'running'
  const searched = data && (data.summary.groups > 0 || data.summary.ignored > 0 || search.state === 'done')

  return (
    <div className="page">
      <PageHead title="Duplicates"
                lead="Identical files inside your sorted folders. Keep one copy in its right place; the others go to an undoable trash.">
        <select value={root} onChange={e => setRoot(e.target.value)} disabled={running} aria-label="Where to search">
          {roots.map(r => <option key={r} value={r}>{r}</option>)}
          <option value="">All category folders</option>
        </select>
        {running
          ? <button onClick={() => api.cancelDuplicateSearch().then(setSearch).catch(fail)}><Icon name="x" size={15} />Cancel</button>
          : <button className="primary" onClick={startSearch}><Icon name="search" size={15} />{searched ? 'Search again' : 'Find duplicates'}</button>}
      </PageHead>

      {running && <SearchProgress s={search} />}
      {search.state === 'failed' && <div className="banner error" role="alert"><Icon name="alert" />Search failed: {search.error}</div>}
      {!running && search.state === 'cancelled' && <p className="muted small">Search cancelled; showing what was found so far.</p>}

      {data && (
        <div className="stats">
          <div className="stat"><span className="stat-value">{data.summary.groups.toLocaleString()}</span><span className="stat-label">sets of identical files</span></div>
          <div className="stat"><span className="stat-value">{data.summary.copies.toLocaleString()}</span><span className="stat-label">extra copies</span></div>
          <div className="stat"><span className="stat-value">{humanSize(data.summary.bytes)}</span><span className="stat-label">space to free</span></div>
          <div className="stat"><span className="stat-value">{data.summary.clear.toLocaleString()}</span><span className="stat-label">clear suggestions</span></div>
        </div>
      )}

      {!searched && !running && (
        <section className="card">
          <p>
            Search once to find every set of identical files (same content, any name). Only files whose size occurs
            more than once are read, at low priority. Later searches only read new or changed files.
          </p>
        </section>
      )}

      {data && searched && (
        <section className="card">
          <div className="section-head">
            <div className="seg" role="tablist">
              {([['files', 'Files', data.summary.groups], ['folders', 'Folders', data.pairs.length],
                 ['kept', 'Kept as is', data.summary.ignored]] as const).map(([k, label, n]) => (
                <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? 'active' : ''} onClick={() => setTab(k)}>
                  {label}<span className="count">{n}</span>
                </button>
              ))}
            </div>
            {tab === 'files' && (
              <button className="primary" disabled={!clear.length || running} onClick={() => setConfirm({
                title: 'Keep the clear suggestions?',
                text: `${clear.reduce((n, g) => n + g.copies.length - 1, 0)} copies (${humanSize(clearBytes)}) in ${clear.length} sets `
                  + 'go to the trash; the suggested copy of each set stays.',
                run: () => api.resolveSuggested(scope),
              })}>
                <Icon name="check" size={15} />Keep {clear.length} clear suggestions…
              </button>
            )}
          </div>

          {tab === 'files' && (
            <>
              <div className="filters dup-filters">
                {(['all', 'clear', 'look'] as const).map(v => (
                  <button key={v} className={`chip${verdict === v ? ' active' : ''}`} onClick={() => setVerdict(v)}>
                    {v === 'all' ? 'All' : v === 'clear' ? 'Clear suggestion' : 'Needs a look'}
                  </button>
                ))}
                <span className="sep" />
                {(['any', 'folder', 'category', 'categories'] as const).map(v => (
                  <button key={v} className={`chip${spread === v ? ' active' : ''}`} onClick={() => setSpread(v)}>
                    {v === 'any' ? 'Anywhere' : SPREAD_LABEL[v]}
                  </button>
                ))}
              </div>
              <ShowMore items={groups} limit={20} className="simple-list dup-list"
                        empty={<p className="muted small">{data.summary.groups ? 'Nothing matches these filters.' : 'No duplicates found.'}</p>}
                        render={g => (
                          <GroupCard key={g.hash} g={g} onIgnore={() => ignore([g.hash])}
                                     onTrash={keep => act(() => api.resolveDuplicates([{ hash: g.hash, keep }], scope))} />
                        )} />
            </>
          )}

          {tab === 'folders' && (
            <>
              <p className="muted small">
                Folders that share identical files, most space first. Decide once for the whole folder pair: the copies in the
                other folder go to the trash. Copies elsewhere stay.
              </p>
              <ShowMore items={data.pairs} limit={10} className="simple-list entries"
                        empty={<p className="muted small">No two folders share identical files.</p>}
                        render={p => (
                          <PairRow key={`${p.a}|${p.b}`} p={p} onKeep={(keep, drop, dropLabel) => setConfirm({
                            title: 'Trash the copies in this folder?',
                            text: `${p.files} copies (${humanSize(p.bytes)}) in ${dropLabel} go to the trash; the identical files in the other folder stay.`,
                            run: () => api.resolveFolders(keep, drop, scope),
                          })} />
                        )} />
            </>
          )}

          {tab === 'kept' && (
            <ShowMore items={kept} limit={10} className="simple-list entries"
                      empty={<p className="muted small">Sets you keep as they are ("Keep all") are listed here.</p>}
                      render={g => (
                        <li key={g.hash}>
                          <div className="entry-main">
                            <span className="entry-title mono">{fileName(g.copies[0].relpath)}</span>
                            <span className="entry-sub">{g.copies.length} copies · {humanSize(g.size)} each · {SPREAD_LABEL[g.spread]}</span>
                          </div>
                          <button onClick={() => ignore([g.hash], false)}>Show again</button>
                        </li>
                      )} />
          )}
        </section>
      )}

      {confirm && (
        <Modal title={confirm.title} onClose={() => setConfirm(null)}>
          <p>{confirm.text}</p>
          <p className="muted small">
            Each copy moves to a hidden <code>.classifiles-trash</code> folder next to it and is checked right before
            (still there, still identical). Undo in History &amp; trash brings them back.
          </p>
          <div className="modal-actions">
            <button onClick={() => setConfirm(null)}>Cancel</button>
            <button className="danger" onClick={() => { const c = confirm; setConfirm(null); act(c.run) }}>
              <Icon name="trash" size={15} />Move to trash
            </button>
          </div>
        </Modal>
      )}
    </div>
  )
}
