import { useEffect, useState } from 'react'
import { api, humanSize, type Proposal } from '../api'
import { Modal } from './Modal'

const TEXT_KINDS = new Set(['text', 'code', 'data'])
const BROWSER_IMAGES = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'])

/** Shows the file itself: images and PDFs inline, text as text, everything else as metadata. */
export function Preview({ p, onClose, onError }: { p: Proposal; onClose: () => void; onError: (m: string) => void }) {
  const url = api.fileUrl(p.id)
  const [text, setText] = useState<string | null>(null)
  const ext = p.evidence.ext
  const isImage = BROWSER_IMAGES.has(ext)
  const isPdf = ext === '.pdf'
  const isText = TEXT_KINDS.has(p.kind)

  useEffect(() => {
    if (!isText) return
    fetch(url).then(r => (r.ok ? r.text() : Promise.reject(r.statusText)))
      .then(t => setText(t.slice(0, 20000)))
      .catch(() => setText(null))
  }, [url, isText])

  const open = (reveal: boolean) => api.openFile(p.id, reveal).catch(e => onError((e as Error).message))

  return (
    <Modal title={p.name} onClose={onClose} wide>
      <div className="preview-meta muted small">
        {p.kind} · {humanSize(p.size)} · modified {p.evidence.modified?.slice(0, 10)}
        {Object.entries(p.evidence.meta ?? {})
          .filter(([k]) => k !== 'extract_error')
          .map(([k, v]) => ` · ${k.replace('_', ' ')}: ${Array.isArray(v) ? v.join(', ') : String(v)}`)}
      </div>
      <div className="preview-body">
        {isImage && <img src={url} alt={p.name} />}
        {isPdf && <iframe src={url} title={p.name} />}
        {isText && <pre>{text ?? 'Loading…'}</pre>}
        {!isImage && !isPdf && !isText && (
          <div className="no-preview">
            <p>No preview for this file type in the browser.</p>
            {p.evidence.text && <><p className="muted small">Text the model read:</p><pre>{p.evidence.text}</pre></>}
          </div>
        )}
      </div>
      <div className="modal-actions">
        <button onClick={() => open(true)}>Show in Explorer</button>
        <button className="primary" onClick={() => open(false)}>Open in Windows</button>
      </div>
    </Modal>
  )
}
