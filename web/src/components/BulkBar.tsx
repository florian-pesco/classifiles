import { useState } from 'react'
import type { Category, Proposal, ReviewAction } from '../api'
import { CategorySelect } from './CategorySelect'
import { PathDialog } from './PathEditor'
import { Icon } from './Icon'

/** Actions for the selected rows: accept, set category / folder, keep in inbox, reset. */
export function BulkBar({ selected, categories, onAction, onClear }: {
  selected: Proposal[]
  categories: Category[]
  onAction: (action: ReviewAction, folder?: string, subpath?: string, useSuggestedName?: boolean) => void
  onClear: () => void
}) {
  const [folderFor, setFolderFor] = useState<string | null>(null)
  const open = selected.filter(p => p.status !== 'moved')
  const targets = [...new Set(open.map(p => p.final_target).filter(Boolean))] as string[]
  const common = targets.length === 1 && open.every(p => p.final_target === targets[0]) ? targets[0] : null
  const commonCat = categories.find(c => c.name === common)
  const withoutCategory = open.filter(p => !p.final_target).length
  const withName = open.filter(p => p.suggested_name).length

  return (
    <div className="bulk-bar" role="toolbar" aria-label="Selected files">
      <b className="bulk-count">{selected.length} selected</b>
      {open.length < selected.length && <span className="muted small">({selected.length - open.length} already applied)</span>}
      <button className="primary" onClick={() => onAction('accept')} disabled={!open.length || withoutCategory > 0}
              title={withoutCategory ? `${withoutCategory} selected files have no category yet` : 'Accept each file as suggested'}>
        <Icon name="check" size={16} />Accept
      </button>
      <CategorySelect value={null} categories={categories} placeholder="Move all to…" label="Category for the selected files"
                      onChange={name => name && onAction('accept', name)} disabled={!open.length} />
      <button onClick={() => setFolderFor(common)} disabled={!commonCat || commonCat.source === 'delete'}
              title={common ? `Choose one folder inside ${common} for all of them` : 'Only when all selected files have the same category'}>
        Folder…
      </button>
      {withName > 0 && (
        <>
          <button onClick={() => onAction('accept', undefined, undefined, true)} disabled={withoutCategory > 0}
                  title={`Use the suggested file name for ${withName} of them (and accept them)`}>
            Use suggested names ({withName})
          </button>
          <button onClick={() => onAction('accept', undefined, undefined, false)} disabled={withoutCategory > 0}
                  title="Keep their original file names">
            Keep original names
          </button>
        </>
      )}
      <button onClick={() => onAction('reject')} disabled={!open.length}><Icon name="x" size={16} />Keep in inbox</button>
      <button onClick={() => onAction('reset')} disabled={!open.length} title="Back to the model's suggestion"><Icon name="undo" size={16} />Reset</button>
      <span className="grow" />
      <button className="linkish" onClick={onClear}>clear selection</button>
      {folderFor && (
        <PathDialog category={folderFor} start=""
                    onCancel={() => setFolderFor(null)}
                    onSave={sub => { setFolderFor(null); onAction('accept', folderFor, sub) }} />
      )}
    </div>
  )
}
