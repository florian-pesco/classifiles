import { useEffect, useState } from 'react'
import { api, humanSize, type Proposal } from '../api'
import { Modal } from './Modal'

const TEXT_KINDS = new Set(['text', 'code', 'data'])
const BROWSER_IMAGES = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'])

/** The file itself: images and PDFs inline, text as text, otherwise a note (plus extracted text). */
export function FileView({ url, name, ext, kind, fallbackText, compact = false }: {
  url: string
  name: string
  ext: string
  kind: string
  fallbackText?: string
  compact?: boolean
}) {
  const [text, setText] = useState<string | null>(null)
  const isImage = BROWSER_IMAGES.has(ext)
  const isPdf = ext === '.pdf'
  const isText = TEXT_KINDS.has(kind)

  useEffect(() => {
    if (!isText) return
    let alive = true
    fetch(url).then(r => (r.ok ? r.text() : Promise.reject(r.statusText)))
      .then(t => { if (alive) setText(t.slice(0, 20000)) })
      .catch(() => { if (alive) setText(null) })
    return () => { alive = false }
  }, [url, isText])

  return (
    <div className={compact ? 'preview-body compact' : 'preview-body'}>
      {isImage && <img src={url} alt={name} />}
      {isPdf && <iframe src={url} title={name} />}
      {isText && <pre>{text ?? 'Loading…'}</pre>}
      {!isImage && !isPdf && !isText && (
        <div className="no-preview">
          <p>No preview for this file type in the browser.</p>
          {fallbackText && <><p className="muted small">Text read from it:</p><pre>{fallbackText}</pre></>}
        </div>
      )}
    </div>
  )
}

/** Shows the file itself, with its metadata and buttons to open it in Windows. */
export function Preview({ p, onClose, onError }: { p: Proposal; onClose: () => void; onError: (m: string) => void }) {
  const open = (reveal: boolean) => api.openFile(p.id, reveal).catch(e => onError((e as Error).message))

  return (
    <Modal title={p.name} onClose={onClose} wide>
      <div className="preview-meta muted small">
        {p.kind} · {humanSize(p.size)} · modified {p.evidence.modified?.slice(0, 10)}
        {Object.entries(p.evidence.meta ?? {})
          .filter(([k]) => k !== 'extract_error')
          .map(([k, v]) => ` · ${k.replace('_', ' ')}: ${Array.isArray(v) ? v.join(', ') : String(v)}`)}
      </div>
      <FileView url={api.fileUrl(p.id)} name={p.name} ext={p.evidence.ext} kind={p.kind} fallbackText={p.evidence.text} />
      <div className="modal-actions">
        <button onClick={() => open(true)}>Show in Explorer</button>
        <button className="primary" onClick={() => open(false)}>Open in Windows</button>
      </div>
    </Modal>
  )
}
