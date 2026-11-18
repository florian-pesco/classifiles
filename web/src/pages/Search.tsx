import { Fragment, useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, humanSize, type SearchGroup, type SearchRequest, type SearchResponse, type SearchStatus } from '../api'
import { Icon } from '../components/Icon'
import { kindIcon } from '../components/kindIcon'
import { PageHead } from '../components/PageHead'
import { useMessages } from '../messages'

const GROUPS: [SearchGroup, string][] = [
  ['documents', 'Documents'], ['pictures', 'Pictures'], ['videos', 'Videos'], ['audio', 'Audio'],
  ['archives', 'Archives'], ['installers', 'Installers'], ['code', 'Code & data'], ['other', 'Other'],
]

type Period = 'any' | 'day' | 'week' | 'month' | 'year' | 'this-year' | 'last-year' | 'custom'
const PERIODS: [Period, string][] = [
  ['any', 'Any time'], ['day', 'Today'], ['week', 'Past 7 days'], ['month', 'Past 30 days'], ['year', 'Past 12 months'],
  ['this-year', 'This year'], ['last-year', 'Last year'], ['custom', 'Between…'],
]
const STALE_HOURS = 6  // the index is updated (in the background) when the page opens and it is older
const PAGE = 100

/** [after, before] in epoch seconds for a period. */
function range(period: Period, from: string, to: string): [number | null, number | null] {
  const now = new Date()
  const ago = (days: number) => Date.now() / 1000 - days * 86400
  const jan1 = (year: number) => new Date(year, 0, 1).getTime() / 1000
  switch (period) {
    case 'day': return [new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000, null]
    case 'week': return [ago(7), null]
    case 'month': return [ago(30), null]
    case 'year': return [ago(365), null]
    case 'this-year': return [jan1(now.getFullYear()), null]
    case 'last-year': return [jan1(now.getFullYear() - 1), jan1(now.getFullYear())]
    case 'custom': return [from ? new Date(from).getTime() / 1000 : null, to ? new Date(to).getTime() / 1000 + 86400 : null]
    default: return [null, null]
  }
}

/** /mnt/d/Archive/x.pdf -> D:\Archive\x.pdf (what Explorer and Windows apps understand). */
function windowsPath(path: string) {
  const m = /^\/mnt\/([a-z])(\/.*)?$/.exec(path)
  return m ? `${m[1].toUpperCase()}:${(m[2] ?? '\\').replace(/\//g, '\\')}` : path
}

/** The words of the query highlighted in a file name (case-insensitive). */
function highlight(text: string, query: string): ReactNode {
  const words = query.split(/[\s_\-.]+/).filter(w => w.length > 1).map(w => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  if (!words.length) return text
  return text.split(new RegExp(`(${words.join('|')})`, 'gi'))
    .map((part, i) => i % 2 ? <mark key={i}>{part}</mark> : <Fragment key={i}>{part}</Fragment>)
}

/** A content snippet: matched words come between \u0002 and \u0003. */
function snippet(text: string): ReactNode {
  return text.split('\u0002').map((part, i) => {
    const [hit, rest] = i ? part.split('\u0003') : ['', part]
    return <Fragment key={i}>{hit && <mark>{hit}</mark>}{rest}</Fragment>
  })
}

function ago(iso?: string) {
  if (!iso) return 'not yet'
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000)
  return min < 1 ? 'just now' : min < 60 ? `${min} min ago` : min < 1440 ? `${Math.round(min / 60)} h ago` : iso.slice(0, 10)
}

function IndexCard({ status, onUpdate, onCancel, onContent }: {
  status: SearchStatus
  onUpdate: () => void
  onCancel: () => void
  onContent: (on: boolean) => void
}) {
  const running = status.state === 'running'
  const pct = status.total ? (100 * (status.done ?? 0)) / status.total : 0
  return (
    <section className="card search-index">
      <div className="section-head">
        <div>
          <h3>Search index</h3>
          <p className="muted small">
            {status.files.toLocaleString()} files{status.content && <> and the text of {status.texts.toLocaleString()} documents</>}
            {' '}in {status.locations.length} location{status.locations.length === 1 ? '' : 's'} · updated {ago(status.finished_at)}.
            Runs at low priority; only new or changed files are read.
          </p>
        </div>
        {running
          ? <button onClick={onCancel}><Icon name="x" size={15} />Stop</button>
          : <button onClick={onUpdate}><Icon name="learn" size={15} />Update now</button>}
      </div>
      {running && (
        <div className="progress" role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemax={100}>
          <div style={{ width: `${status.phase === 'reading' ? pct : 0}%` }} />
          <span>
            {status.phase === 'reading'
              ? `Reading documents · ${(status.done ?? 0).toLocaleString()}/${(status.total ?? 0).toLocaleString()}`
                + (status.eta_seconds != null ? ` · about ${Math.max(1, Math.round(status.eta_seconds / 60))} min left` : '')
              : status.message ?? 'Listing files…'}
          </span>
        </div>
      )}
      {status.state === 'failed' && <p className="warn-text small">Update failed: {status.error}</p>}
      <label className="check" style={{ marginTop: 10 }}>
        <input type="checkbox" checked={status.content} onChange={e => onContent(e.target.checked)} />
        Search inside documents (PDF, Office and text files; no OCR)
      </label>
    </section>
  )
}

export function SearchPage() {
  const { notify, fail } = useMessages()
  const [text, setText] = useState('')
  const [groups, setGroups] = useState<SearchGroup[]>([])
  const [period, setPeriod] = useState<Period>('any')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [location, setLocation] = useState('')
  const [match, setMatch] = useState<SearchRequest['match']>('all')
  const [sort, setSort] = useState<SearchRequest['sort']>('relevance')
  const [ext, setExt] = useState('')
  const [limit, setLimit] = useState(PAGE)
  const [result, setResult] = useState<SearchResponse | null>(null)
  const [status, setStatus] = useState<SearchStatus | null>(null)
  const input = useRef<HTMLInputElement>(null)
  const latest = useRef(0)

  const run = useCallback(() => {
    const [after, before] = range(period, from, to)
    const ticket = ++latest.current
    api.search({ text, groups, ext, after, before, location: location || null, match, sort, limit })
      .then(r => { if (ticket === latest.current) setResult(r) })  // ignore answers to older queries
      .catch(fail)
  }, [text, groups, ext, period, from, to, location, match, sort, limit, fail])

  // Search as you type (short pause), and whenever a filter changes.
  useEffect(() => {
    const t = setTimeout(run, 200)
    return () => clearTimeout(t)
  }, [run])
  useEffect(() => setLimit(PAGE), [text, groups, ext, period, from, to, location, match, sort])

  // The index: update it in the background when the page opens and it is old (or not built yet).
  useEffect(() => {
    api.searchStatus().then(s => {
      setStatus(s)
      const age = s.finished_at ? (Date.now() - new Date(s.finished_at).getTime()) / 3.6e6 : Infinity
      if (s.state !== 'running' && age > STALE_HOURS) api.updateSearch().then(setStatus).catch(fail)
    }).catch(fail)
  }, [fail])
  useEffect(() => {
    if (status?.state !== 'running') return
    const t = setInterval(() => api.searchStatus().then(s => { setStatus(s); if (s.state !== 'running') run() }).catch(fail), 1500)
    return () => clearInterval(t)
  }, [status?.state, run, fail])

  // "/" jumps to the search box.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === '/' && !(e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement)) {
        e.preventDefault()
        input.current?.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  const toggleGroup = (g: SearchGroup) => setGroups(gs => gs.includes(g) ? gs.filter(x => x !== g) : [...gs, g])
  const setContent = (on: boolean) => api.saveSettings({ search_content: on })
    .then(() => api.updateSearch()).then(setStatus)
    .then(() => notify(on ? 'Reading document text in the background.' : 'Document text removed from the index.'))
    .catch(fail)
  const copy = (path: string) => navigator.clipboard.writeText(windowsPath(path))
    .then(() => notify('Path copied.'), () => fail('Could not copy the path.'))
  const filtered = text.trim() || groups.length || ext || period !== 'any' || location

  return (
    <div className="page">
      <PageHead title="Search" lead="Find files in your folders and inboxes by name, folder or content, with filters for type and date. Runs on your PC." />

      <div className="search-box">
        <Icon name="search" size={19} className="muted" />
        <input ref={input} autoFocus value={text} onChange={e => setText(e.target.value)} aria-label="Search"
               placeholder="Name, folder or words in a document — e.g. rechnung telekom 2024" />
        {text && <button className="icon" onClick={() => { setText(''); input.current?.focus() }} aria-label="Clear"><Icon name="x" size={16} /></button>}
      </div>

      <div className="filters search-filters">
        <button className={`chip${groups.length ? '' : ' active'}`} onClick={() => setGroups([])}>All types</button>
        {GROUPS.map(([g, label]) => (
          <button key={g} className={`chip${groups.includes(g) ? ' active' : ''}`} onClick={() => toggleGroup(g)}
                  aria-pressed={groups.includes(g)}>{label}</button>
        ))}
      </div>
      <div className="filters search-filters">
        <select value={period} onChange={e => setPeriod(e.target.value as Period)} aria-label="Modified">
          {PERIODS.map(([p, label]) => <option key={p} value={p}>{p === 'any' ? 'Modified: any time' : label}</option>)}
        </select>
        {period === 'custom' && (
          <>
            <input type="date" value={from} onChange={e => setFrom(e.target.value)} aria-label="From" />
            <span className="muted small">to</span>
            <input type="date" value={to} onChange={e => setTo(e.target.value)} aria-label="To" />
          </>
        )}
        <select value={location} onChange={e => setLocation(e.target.value)} aria-label="Where">
          <option value="">Everywhere</option>
          {status?.locations.map(l => <option key={l} value={l} title={l}>{l.split('/').filter(Boolean).pop() ?? l}</option>)}
        </select>
        <select value={match} onChange={e => setMatch(e.target.value as SearchRequest['match'])} aria-label="Match">
          <option value="all">Name and content</option>
          <option value="name">Name and folder only</option>
          <option value="content">Content only</option>
        </select>
        <select value={sort} onChange={e => setSort(e.target.value as SearchRequest['sort'])} aria-label="Sort">
          <option value="relevance">Best match</option>
          <option value="newest">Newest first</option>
          <option value="oldest">Oldest first</option>
          <option value="largest">Largest first</option>
          <option value="name">Name</option>
        </select>
        <input className="ext-input" value={ext} onChange={e => setExt(e.target.value)} placeholder="Extension, e.g. dwg" aria-label="Extension" />
      </div>

      {result && (
        <section className="card">
          <p className="muted small search-meta">
            {filtered
              ? `${result.more ? 'More than ' : ''}${result.total.toLocaleString()} file${result.total === 1 ? '' : 's'} · ${result.took_ms} ms`
              : 'Recently changed files'}
          </p>
          {result.results.length === 0
            ? <p className="muted">Nothing found.{status && status.files === 0 && ' The search index is still being built.'}</p>
            : (
              <ul className="simple-list search-results">
                {result.results.map(r => (
                  <li key={r.id}>
                    <Icon name={kindIcon(r.kind)} size={20} className="muted kind-ico" />
                    <div className="entry-main">
                      <span className="entry-title">
                        <span className="search-name">{highlight(r.name, text)}</span>
                        {r.in_content && <span className="badge plain learned">in content</span>}
                        {r.in_folder && !r.in_name && <span className="badge plain">in folder</span>}
                      </span>
                      <span className="entry-sub" title={windowsPath(r.path)}>
                        {highlight(r.folder, match === 'content' ? '' : text)} · {r.modified.replace('T', ' ')} · {humanSize(r.size)}
                      </span>
                      {r.snippet && <span className="search-snippet small">{snippet(r.snippet)}</span>}
                    </div>
                    <span className="rowbtns">
                      <button className="icon" title="Open" onClick={() => api.openResult(r.id).catch(fail)}><Icon name="external" size={15} /></button>
                      <button className="icon" title="Show in Explorer" onClick={() => api.openResult(r.id, true).catch(fail)}><Icon name="folder" size={15} /></button>
                      <button className="icon" title="Copy the Windows path" onClick={() => copy(r.path)}><Icon name="copy" size={15} /></button>
                    </span>
                  </li>
                ))}
              </ul>
            )}
          {result.results.length < result.total && result.results.length === limit && (
            <button className="ghost show-more" onClick={() => setLimit(l => Math.min(l + PAGE, 1000))}>Show more</button>
          )}
        </section>
      )}

      {status && (
        <IndexCard status={status} onContent={setContent}
                   onUpdate={() => api.updateSearch().then(setStatus).catch(fail)}
                   onCancel={() => api.cancelSearchUpdate().then(setStatus).catch(fail)} />
      )}
    </div>
  )
}
