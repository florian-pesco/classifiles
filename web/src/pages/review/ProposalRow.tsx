import { useState } from 'react'
import { humanSize, type Category, type Proposal, type ReviewAction } from '../../api'
import { CategorySelect } from '../../components/CategorySelect'
import { Icon } from '../../components/Icon'
import { kindIcon } from '../../components/kindIcon'
import { NameEditor } from '../../components/NameEditor'
import { PathEditor } from '../../components/PathEditor'
import { STATUS_LABEL } from '../../labels'

const APPLIED_LABEL = { move: 'Moved', copy: 'Copied', delete: 'In trash' } as const

/** One file in the Review list: category, folder, name, confidence and the accept/keep buttons. */
export function ProposalRow({ p, categories, threshold, selected, onSelect, onReview, onPreview, onCompare }: {
  p: Proposal
  categories: Category[]
  threshold: number
  selected: boolean
  onSelect: (shift: boolean) => void
  onReview: (p: Proposal, action: ReviewAction, folder?: string, subpath?: string,
             name?: string, useSuggestedName?: boolean) => void
  onPreview: () => void
  onCompare: () => void
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
  const showName = cat && cat.source !== 'delete' && !p.duplicate_of && (p.suggested_name || p.new_name)

  return (
    <li className={`row status-${p.status}${p.duplicate_of ? ' is-dup' : ''}${selected ? ' selected' : ''}`}>
      <input type="checkbox" className="pick" checked={selected} aria-label={`Select ${p.name}`}
             onChange={() => undefined} onClick={e => onSelect(e.shiftKey)} />
      <div className="kind-ico" title={p.kind}><Icon name={kindIcon(p.kind)} /></div>
      <div className="file">
        <div className="file-name">
          <button className="linkish name" onClick={() => setOpen(o => !o)} aria-expanded={open} title={p.name}>{p.name}</button>
          {p.relearned === 1 && !done && (
            <span className="badge learned plain"
                  title="Changed by a decision you made in this scan: a similar file you sorted moved this suggestion. Open details to see which.">
              <Icon name="learn" size={12} />updated
            </span>
          )}
        </div>
        <div className="meta">
          <span>{p.kind}{looks ? ` · ${looks}` : ''}</span>
          <span>{humanSize(p.size)}</span>
          <span>{p.evidence.modified?.slice(0, 10)}</span>
          {source && <span title="Where it was downloaded from">from {source}</span>}
          {viaOcr && <span title="Text read by OCR (scanned PDF)">OCR</span>}
          <button className="linkish toggle-details" onClick={() => setOpen(o => !o)} aria-expanded={open}>
            <Icon name="chevron" size={13} />details
          </button>
          <button className="linkish" onClick={onPreview}>preview</button>
        </div>
      </div>

      <div className="conf" title={`${p.source}: ${(p.confidence * 100).toFixed(0)}%`}>
        <div className="conf-top"><span>{(p.confidence * 100).toFixed(0)}%</span>{p.source === 'rule' && <span>rule</span>}</div>
        <div className="bar"><div className={confClass} style={{ width: `${p.confidence * 100}%` }} /></div>
      </div>

      <CategorySelect value={p.final_target} categories={categories} disabled={done}
                      onChange={name => name && onReview(p, 'accept', name)} label={`Category for ${p.name}`} />

      <span className={`badge status ${p.status}`}>{statusLabel}</span>

      <div className="rowbtns">
        {!done && p.status !== 'accepted' && (
          <button className="icon ok" onClick={() => onReview(p, 'accept')} disabled={!p.final_target} title="Accept" aria-label="Accept">
            <Icon name="check" />
          </button>
        )}
        {!done && p.status !== 'rejected' && (
          <button className="icon no" onClick={() => onReview(p, 'reject')} title="Keep in inbox" aria-label="Keep in inbox">
            <Icon name="x" />
          </button>
        )}
        {!done && (p.status === 'accepted' || p.status === 'rejected') && (
          <button className="icon" onClick={() => onReview(p, 'reset')} title="Reset" aria-label="Reset"><Icon name="undo" /></button>
        )}
      </div>

      <div className="row-sub">
        {p.duplicate_of && (
          <div className="dup-line">
            <span className="dup-label">
              <Icon name="copy" size={15} />
              {p.duplicate_kind === 'sorted' ? 'Identical file already in your folders:' : 'Identical file also in the inbox:'}
            </span>
            <span className="mono dup-path" title={p.duplicate_of}>{p.duplicate_of}</span>
            <button className="compare-btn" onClick={onCompare}>Compare</button>
          </div>
        )}
        {cat && cat.source !== 'delete' && (
          <PathEditor category={cat.name} subpath={p.subpath ?? ''} suggested={p.suggested_subpath ?? ''}
                      newFolder={p.new_folder} disabled={done}
                      onChange={sub => onReview(p, 'accept', p.final_target ?? undefined, sub)} />
        )}
        {showName && (
          <NameEditor original={p.name} suggested={p.suggested_name} current={p.new_name} reason={p.name_reason}
                      disabled={done}
                      onUse={() => onReview(p, 'accept', p.final_target ?? undefined, undefined, undefined, true)}
                      onKeep={() => onReview(p, 'accept', p.final_target ?? undefined, undefined, undefined, false)}
                      onSet={n => onReview(p, 'accept', p.final_target ?? undefined, undefined, n)} />
        )}
        {p.llm_choice && (
          <div className="second-opinion small">
            {llmAgrees
              ? <span className="badge so-agree" title="The language model chose the same category">both agree</span>
              : (
                <>
                  <span className="badge so-differ" title="The language model chose a different category">2nd opinion: {p.llm_choice}</span>
                  {!done && <button className="linkish" onClick={() => onReview(p, 'accept', p.llm_choice!)}>use it</button>}
                </>
              )}
          </div>
        )}
      </div>

      {open && (
        <dl className="details">
          {p.reason && <><dt>Why</dt><dd>{p.reason}</dd></>}
          {p.alternatives.length > 0 && (
            <><dt>Scores</dt><dd>{p.alternatives.map(a => `${a.folder} ${(a.probability * 100).toFixed(0)}%`).join(' · ')}</dd></>
          )}
          {p.name_reason && p.suggested_name && (
            <><dt>Name</dt><dd><span className="mono">{p.suggested_name}</span>: {p.name_reason}</dd></>
          )}
          {done && p.applied_dst && <><dt>Now at</dt><dd className="mono">{p.applied_dst}</dd></>}
          <dt>Content</dt>
          <dd>
            {p.evidence.text
              ? <div className="snippet">{p.evidence.text.slice(0, 400)}</div>
              : <span className="warn-text">No readable content: judged by file name and type only.</span>}
          </dd>
        </dl>
      )}
    </li>
  )
}
