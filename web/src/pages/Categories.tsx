import { useEffect, useMemo, useState } from 'react'
import { api, type CategoriesPayload, type CategoryEntry, type DeleteSettings } from '../api'
import { FolderPicker } from '../components/FolderPicker'

interface Row {
  path: string
  label: string  // what is shown / used in rules
  name: string   // editable name (manual categories only)
  description: string
  enabled: boolean
  action: 'move' | 'copy'
  source: 'auto' | 'manual'
  exists: boolean
  root: string | null
  samples: string[]
}

const WORD_LIMIT = 12

function toRows(p: CategoriesPayload): Row[] {
  return p.categories
    .filter(c => c.source !== 'delete')
    .map(c => ({
      path: c.path!, label: c.name, name: c.source === 'manual' ? c.name : '', description: c.description,
      enabled: c.enabled, action: c.action === 'copy' ? 'copy' : 'move', source: c.source as 'auto' | 'manual',
      exists: c.exists, root: c.root, samples: c.samples,
    }))
}

const words = (s: string) => s.trim().split(/\s+/).filter(Boolean).length

export function CategoriesPage({ data, onSaved, notify, fail }: {
  data: CategoriesPayload
  onSaved: (p: CategoriesPayload) => void
  notify: (m: string) => void
  fail: (e: unknown) => void
}) {
  const [roots, setRoots] = useState(data.roots)
  const [rows, setRows] = useState<Row[]>(() => toRows(data))
  const [del, setDel] = useState<DeleteSettings>(data.delete)
  const [dirty, setDirty] = useState(false)
  const [picker, setPicker] = useState<'root' | 'manual' | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    setRoots(data.roots); setRows(toRows(data)); setDel(data.delete); setDirty(false)
  }, [data])

  const edit = (path: string, patch: Partial<Row>) => {
    setRows(rs => rs.map(r => (r.path === path ? { ...r, ...patch } : r)))
    setDirty(true)
  }

  const save = async () => {
    setSaving(true)
    try {
      const entries: CategoryEntry[] = rows.map(r => ({
        path: r.path,
        ...(r.source === 'manual' && r.name.trim() ? { name: r.name.trim() } : {}),
        description: r.description,
        enabled: r.enabled,
        action: r.action,
      }))
      const saved = await api.saveCategories(roots, entries, del)
      onSaved(saved)
      notify('Categories saved. They apply to the next scan.')
    } catch (e) { fail(e) } finally { setSaving(false) }
  }

  const reload = async () => {
    try { onSaved(await api.categories(true)); notify('Folders re-read.') } catch (e) { fail(e) }
  }

  const grouped = useMemo(() => {
    const g = new Map<string, Row[]>()
    for (const r of rows) {
      const key = r.source === 'manual' ? 'Manual categories' : r.root ?? '?'
      g.set(key, [...(g.get(key) ?? []), r])
    }
    return [...g.entries()].sort(([a], [b]) => (a === 'Manual categories' ? 1 : b === 'Manual categories' ? -1 : a.localeCompare(b)))
  }, [rows])

  return (
    <div className="page">
      <section className="card explain">
        <h2>Categories</h2>
        <p>
          Categories are the places files can go. Every subfolder of a <b>root</b> becomes a category
          automatically. You can also add any other folder as a <b>manual</b> category. For each one,
          set a short <b>description</b> (what belongs there, about {WORD_LIMIT} words; the model reads
          little more), switch it <b>off</b> to never offer it, and choose <b>move</b> or <b>copy</b>{' '}
          (copy keeps the original in the inbox).
        </p>
      </section>

      <section className="card">
        <div className="section-head">
          <h3>Roots</h3>
          <button onClick={() => setPicker('root')}>+ Add root</button>
        </div>
        {roots.length === 0 && <p className="muted small">No roots yet. Add the folder that holds your category folders.</p>}
        <ul className="simple-list">
          {roots.map(r => (
            <li key={r.path}>
              <span className="mono">{r.path}</span>
              <button className="icon no" title="Remove root" onClick={() => {
                setRoots(rs => rs.filter(x => x.path !== r.path)); setDirty(true)
              }}>✕</button>
            </li>
          ))}
        </ul>
        {dirty && roots.some(r => !rows.some(x => x.root === r.path)) && (
          <p className="muted small">Save to load the subfolders of new roots.</p>
        )}
      </section>

      {grouped.map(([group, items]) => (
        <section className="card" key={group}>
          <div className="section-head">
            <h3 className={group === 'Manual categories' ? '' : 'mono'}>{group}</h3>
            {group === 'Manual categories' && <button onClick={() => setPicker('manual')}>+ Add folder</button>}
          </div>
          <div className="cat-table">
            {items.map(r => {
              const n = words(r.description)
              return (
                <div key={r.path} className={`cat-row${r.enabled ? '' : ' off'}`}>
                  <label className="check" title={r.enabled ? 'Offered as category' : 'Not offered'}>
                    <input type="checkbox" checked={r.enabled} onChange={e => edit(r.path, { enabled: e.target.checked })} />
                  </label>
                  <div className="cat-name">
                    {r.source === 'manual'
                      ? <input value={r.name} placeholder={r.path.split('/').pop()} aria-label="Category name"
                               onChange={e => edit(r.path, { name: e.target.value })} />
                      : <b>{r.label}</b>}
                    {r.source === 'manual' && <div className="muted small mono">{r.path}</div>}
                    {!r.exists && <div className="warn-text small">folder missing</div>}
                  </div>
                  <div className="cat-desc">
                    <input value={r.description} aria-label={`Description for ${r.label}`}
                           placeholder={r.samples.length ? `e.g. ${r.samples.join(', ')}` : 'what belongs here?'}
                           onChange={e => edit(r.path, { description: e.target.value })} />
                    <span className={`small ${n > WORD_LIMIT ? 'warn-text' : 'muted'}`}>
                      {n === 0 ? 'no description: the model only sees the folder name' : `${n} words`}
                      {n > WORD_LIMIT ? ` (the model may not read past ~${WORD_LIMIT})` : ''}
                    </span>
                  </div>
                  <select value={r.action} onChange={e => edit(r.path, { action: e.target.value as 'move' | 'copy' })}
                          aria-label={`Action for ${r.label}`}>
                    <option value="move">move</option>
                    <option value="copy">copy</option>
                  </select>
                  {r.source === 'manual'
                    ? <button className="icon no" title="Remove category" onClick={() => {
                        setRows(rs => rs.filter(x => x.path !== r.path)); setDirty(true)
                      }}>✕</button>
                    : <span />}
                </div>
              )
            })}
          </div>
        </section>
      ))}
      {!grouped.some(([g]) => g === 'Manual categories') && (
        <section className="card">
          <div className="section-head">
            <h3>Manual categories</h3>
            <button onClick={() => setPicker('manual')}>+ Add folder</button>
          </div>
          <p className="muted small">Any folder outside your roots, e.g. on another drive.</p>
        </section>
      )}

      <section className="card">
        <h3>🗑 Delete</h3>
        <p className="muted small">
          A built-in category for junk. "Deleted" files first go to a hidden <code>.classifiles-trash</code> folder
          inside the inbox. That can be undone until you empty the trash (History page).
        </p>
        <div className="cat-row">
          <label className="check">
            <input type="checkbox" checked={del.enabled} onChange={e => { setDel({ ...del, enabled: e.target.checked }); setDirty(true) }} />
          </label>
          <div className="cat-name"><b>Delete</b></div>
          <div className="cat-desc">
            <input value={del.description} placeholder="junk: installers, temporary or generated files, duplicates"
                   onChange={e => { setDel({ ...del, description: e.target.value }); setDirty(true) }} aria-label="Delete description" />
            <label className="check small" title="Otherwise only rules and your manual choice send files to Delete">
              <input type="checkbox" checked={del.suggest} onChange={e => { setDel({ ...del, suggest: e.target.checked }); setDirty(true) }} />
              Let the model suggest Delete (otherwise only rules and your own choice)
            </label>
          </div>
          <span /><span />
        </div>
      </section>

      <footer className="actions">
        <button className="primary" onClick={save} disabled={!dirty || saving}>{saving ? 'Saving…' : 'Save categories'}</button>
        <button onClick={() => { setRoots(data.roots); setRows(toRows(data)); setDel(data.delete); setDirty(false) }} disabled={!dirty}>
          Discard changes
        </button>
        <span className="grow" />
        <button onClick={reload} disabled={dirty} title="Re-read folders from disk">Re-read folders</button>
      </footer>

      {picker && (
        <FolderPicker
          title={picker === 'root' ? 'Add root (its subfolders become categories)' : 'Add a folder as category'}
          start={roots[0]?.path}
          onClose={() => setPicker(null)}
          onPick={path => {
            setPicker(null)
            if (picker === 'root') {
              if (!roots.some(r => r.path === path)) setRoots(rs => [...rs, { path }])
            } else if (!rows.some(r => r.path === path)) {
              setRows(rs => [...rs, {
                path, label: path.split('/').pop() ?? path, name: '', description: '', enabled: true,
                action: 'move', source: 'manual', exists: true, root: null, samples: [],
              }])
            }
            setDirty(true)
          }}
        />
      )}
    </div>
  )
}
