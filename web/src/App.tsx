import { useCallback, useEffect, useState } from 'react'
import { api, type CategoriesPayload, type Settings } from './api'
import { Icon, type IconName } from './components/Icon'
import { CategoriesPage } from './pages/Categories'
import { HistoryPage } from './pages/History'
import { ReviewPage } from './pages/Review'
import { RulesPage } from './pages/Rules'
import { SettingsPage } from './pages/Settings'

type Page = 'review' | 'categories' | 'rules' | 'history' | 'settings'
const PAGES: { key: Page; label: string; icon: IconName }[] = [
  { key: 'review', label: 'Review', icon: 'inbox' },
  { key: 'categories', label: 'Categories', icon: 'layers' },
  { key: 'rules', label: 'Rules', icon: 'rules' },
  { key: 'history', label: 'History & trash', icon: 'history' },
  { key: 'settings', label: 'Settings', icon: 'settings' },
]

type Theme = 'auto' | 'light' | 'dark'
function storedTheme(): Theme {
  try {
    const t = localStorage.getItem('classifiles-theme')
    return t === 'light' || t === 'dark' ? t : 'auto'
  } catch { return 'auto' }
}
function applyTheme(t: Theme) {
  if (t === 'auto') document.documentElement.removeAttribute('data-theme')
  else document.documentElement.setAttribute('data-theme', t)
  try { localStorage.setItem('classifiles-theme', t) } catch { /* private mode: just not remembered */ }
}

function initialPage(): Page {
  const h = window.location.hash.slice(1) as Page
  return PAGES.some(p => p.key === h) ? h : 'review'
}

export default function App() {
  const [page, setPageState] = useState<Page>(initialPage)
  const [settings, setSettings] = useState<Settings | null>(null)
  const [cats, setCats] = useState<CategoriesPayload | null>(null)
  const [scanId, setScanId] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [theme, setTheme] = useState<Theme>(storedTheme)
  useEffect(() => applyTheme(theme), [theme])

  const setPage = (p: Page) => { setPageState(p); window.location.hash = p }
  const fail = useCallback((e: unknown) => setError(e instanceof Error ? e.message : String(e)), [])
  const notify = useCallback((m: string) => { setError(null); setNotice(m) }, [])

  // Follow the address bar (browser back/forward, links to #page).
  useEffect(() => {
    const onHash = () => setPageState(initialPage())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    if (!notice) return
    const t = setTimeout(() => setNotice(null), 5000)
    return () => clearTimeout(t)
  }, [notice])

  useEffect(() => {
    Promise.all([api.settings(), api.categories()])
      .then(([s, c]) => { setSettings(s); setCats(c) })
      .catch(fail)
  }, [fail])

  const needsSetup = cats && cats.roots.length === 0 && !cats.categories.some(c => c.source === 'manual')

  const unreachable = (cats?.unreachable_roots?.length ?? 0) > 0

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark"><Icon name="folder" size={19} /></div>
          <div>
            <div className="brand-name">Classifiles</div>
            <div className="brand-sub">sorts your inbox, locally</div>
          </div>
        </div>
        <nav className="nav" aria-label="Pages">
          {PAGES.map(p => (
            <button key={p.key} className={p.key === page ? 'active' : ''} onClick={() => setPage(p.key)}
                    aria-current={p.key === page ? 'page' : undefined} title={p.label}>
              <Icon name={p.icon} /><span>{p.label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          {cats && (
            <div className="status-line" title={unreachable ? cats.unreachable_roots.join(', ') : 'All category folders are reachable'}>
              <span className={`dot${unreachable ? ' bad' : ''}`} />
              {unreachable ? 'Drive not reachable' : `${cats.categories.filter(c => c.usable && c.source !== 'delete').length} categories ready`}
            </div>
          )}
          <div className="theme-switch" role="group" aria-label="Colour theme">
            {(['light', 'auto', 'dark'] as Theme[]).map(t => (
              <button key={t} className={theme === t ? 'active' : ''} onClick={() => setTheme(t)}
                      title={t === 'auto' ? 'Follow the system' : t === 'light' ? 'Light' : 'Dark'} aria-pressed={theme === t}>
                <Icon name={t === 'auto' ? 'auto' : t === 'light' ? 'sun' : 'moon'} size={15} />
              </button>
            ))}
          </div>
        </div>
      </aside>

      <div className="main">
        <div className="notices">
          {error && (
            <div className="banner error" onClick={() => setError(null)} role="alert">
              <Icon name="alert" /><span className="grow">{error}</span><Icon name="x" size={15} />
            </div>
          )}
          {notice && (
            <div className="banner ok" onClick={() => setNotice(null)} role="status">
              <Icon name="check" /><span className="grow">{notice}</span>
            </div>
          )}
        </div>
        <div className="main-inner">
          {unreachable && (
            <div className="banner error" role="alert" style={{ marginBottom: 18, cursor: 'default' }}>
              <Icon name="alert" />
              <span className="grow">
                Category folder not reachable: <span className="mono">{cats!.unreachable_roots.join(', ')}</span>.
                Is the drive connected? Scans and index updates are paused until it is back.{' '}
                <button className="linkish" onClick={() => api.categories(true).then(setCats).catch(fail)}>Check again</button>
              </span>
            </div>
          )}
          {needsSetup && page === 'review' && (
            <div className="banner info" style={{ marginBottom: 18 }}>
              No categories yet. <button className="linkish" onClick={() => setPage('categories')}>Set up categories</button> first.
            </div>
          )}
          {!settings || !cats ? <p className="empty">Loading…</p> : (
            <main>
              {page === 'review' && (
                <ReviewPage settings={settings} categories={cats.categories} scanId={scanId} setScanId={setScanId}
                            notify={notify} fail={fail} />
              )}
              {page === 'categories' && <CategoriesPage data={cats} examplesActive={settings.backend === 'examples'} onSaved={setCats}
                                                        notify={notify} fail={fail} />}
              {page === 'rules' && <RulesPage categories={cats.categories} notify={notify} fail={fail} />}
              {page === 'history' && (
                <HistoryPage openScan={id => { setScanId(id); setPage('review') }} notify={notify} fail={fail} />
              )}
              {page === 'settings' && (
                <SettingsPage settings={settings} notify={notify} fail={fail}
                              onSaved={s => { setSettings(s); api.categories().then(setCats).catch(fail) }} />
              )}
            </main>
          )}
        </div>
      </div>
    </div>
  )
}
