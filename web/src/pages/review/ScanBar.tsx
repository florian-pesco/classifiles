import { useState } from 'react'
import { api, type Scan, type Settings } from '../../api'
import { PathInput } from '../../components/FolderPicker'
import { Icon } from '../../components/Icon'
import { useMessages } from '../../messages'

/** The scan card: inbox, file limit and "only new", the scan button, and progress while it runs. */
export function ScanBar({ settings, scan, hasScans, onStart }: {
  settings: Settings
  scan: Scan | null
  hasScans: boolean
  onStart: (inbox: string, limit: number | undefined, onlyNew: boolean) => void
}) {
  const { fail } = useMessages()
  const [inbox, setInbox] = useState(settings.inboxes[0] ?? '')
  const [maxFiles, setMaxFiles] = useState(settings.max_files ? String(settings.max_files) : '')
  const [onlyNew, setOnlyNew] = useState(false)
  const [scanForm, setScanForm] = useState(false) // full scan options open (always open before the first scan)
  const running = scan?.status === 'running'

  const startScan = () => {
    const limit = parseInt(maxFiles, 10)
    onStart(inbox, limit > 0 ? limit : undefined, onlyNew)
  }

  return (
    <section className="card scan-card">
      {scanForm || !hasScans ? (
        <div className="scanbar">
          <PathInput value={inbox} onChange={setInbox} label="Inbox folder" placeholder="Inbox folder to scan" />
          <input className="limit" type="number" min={1} value={maxFiles} placeholder="all"
                 onChange={e => setMaxFiles(e.target.value)} aria-label="Maximum number of files"
                 title="Classify at most this many files (newest first). Empty = all." />
          <label className="check" title="Skip files already classified in an earlier scan">
            <input type="checkbox" checked={onlyNew} onChange={e => setOnlyNew(e.target.checked)} /> Only new
          </label>
          <button className="primary" onClick={() => { setScanForm(false); startScan() }} disabled={running}>
            <Icon name="scan" size={16} />{running ? 'Scanning…' : 'Scan & classify'}
          </button>
        </div>
      ) : (
        <div className="scanbar compact">
          <Icon name="inbox" className="muted" />
          <span className="grow scan-summary" title={inbox}>
            <b>{inbox.split('/').filter(Boolean).pop() || 'Inbox'}</b>
            <span className="muted small"> {maxFiles ? `· newest ${maxFiles}` : '· all files'}{onlyNew ? ' · only new' : ''}</span>
          </span>
          <button className="ghost" onClick={() => setScanForm(true)}>Change</button>
          <button className="primary" onClick={startScan} disabled={running}>
            <Icon name="scan" size={16} />{running ? 'Scanning…' : 'Scan & classify'}
          </button>
        </div>
      )}
      {running && (scan?.phase === 'second_opinion' || scan?.phase === 'naming') && (
        <div className="row-form so-progress">
          <div className="progress grow" role="progressbar" aria-valuenow={scan.llm_done} aria-valuemax={scan.llm_total}>
            <div style={{ width: `${scan.llm_total ? (100 * scan.llm_done) / scan.llm_total : 0}%` }} />
            <span>
              {scan.llm_done === 0 ? 'Starting the language model (first time: one-time download)…'
                : scan.phase === 'naming'
                  ? `Choosing file names: ${scan.llm_done} / ${scan.llm_total}`
                  : `Second opinion on unsure files: ${scan.llm_done} / ${scan.llm_total}`}
            </span>
          </div>
          <button onClick={() => api.cancelSecondOpinion().catch(fail)}>Skip</button>
        </div>
      )}
      {scan?.llm_error && <div className="banner error">Language model stopped: {scan.llm_error}</div>}
      {running && scan && scan.phase !== 'second_opinion' && scan.phase !== 'naming' && (
        <div className="progress" role="progressbar" aria-valuenow={scan.processed} aria-valuemax={scan.total}>
          <div style={{ width: `${scan.total ? (100 * scan.processed) / scan.total : 0}%` }} />
          <span>{scan.processed} / {scan.total} files
            {scan.total < scan.inbox_total ? ` (of ${scan.inbox_total} in inbox)` : ''}</span>
        </div>
      )}
      {scan?.status === 'failed' && <div className="banner error">Scan failed: {scan.error}</div>}
    </section>
  )
}
