import { useCallback, useEffect, useState } from 'react'
import { api, type CategoriesPayload, type Settings } from './api'
import { CategoriesPage } from './pages/Categories'
import { HistoryPage } from './pages/History'
import { ReviewPage } from './pages/Review'
import { RulesPage } from './pages/Rules'
import { SettingsPage } from './pages/Settings'

type Page = 'review' | 'categories' | 'rules' | 'history' | 'settings'
const PAGES: { key: Page; label: string }[] = [
  { key: 'review', label: 'Review' },
  { key: 'categories', label: 'Categories' },
  { key: 'rules', label: 'Rules' },
  { key: 'history', label: 'History & trash' },
  { key: 'settings', label: 'Settings' },
]

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

  const setPage = (p: Page) => { setPageState(p); window.location.hash = p }
  const fail = useCallback((e: unknown) => setError(e instanceof Error ? e.message : String(e)), [])
  const notify = useCallback((m: string) => { setError(null); setNotice(m) }, [])

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

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <h1>Classifiles</h1>
          <nav className="tabs" aria-label="Pages">
            {PAGES.map(p => (
              <button key={p.key} className={p.key === page ? 'tab active' : 'tab'} onClick={() => setPage(p.key)}
                      aria-current={p.key === page ? 'page' : undefined}>
                {p.label}
              </button>
            ))}
          </nav>
        </div>
        {error && <div className="banner error" onClick={() => setError(null)} role="alert">{error} <span className="muted">(click to close)</span></div>}
        {notice && <div className="banner ok" onClick={() => setNotice(null)}>{notice}</div>}
        {needsSetup && page === 'review' && (
          <div className="banner info">
            No categories yet. <button className="linkish" onClick={() => setPage('categories')}>Set up categories</button> first.
          </div>
        )}
      </header>

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
  )
}
