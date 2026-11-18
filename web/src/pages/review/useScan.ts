import { type Dispatch, type SetStateAction, useCallback, useEffect, useState } from 'react'
import { api, type Proposal, type Scan } from '../../api'
import { useMessages } from '../../messages'

/** The list of scans and the open scan: loads it with its proposals, polls while it runs. */
export function useScan(scanId: number | null, setScanId: (id: number | null) => void, rows: {
  loadRows: (rows: Proposal[]) => void
  setProposals: Dispatch<SetStateAction<Proposal[]>>
}) {
  const { fail } = useMessages()
  const { loadRows, setProposals } = rows
  const [scans, setScans] = useState<Scan[]>([])
  const [scan, setScan] = useState<Scan | null>(null)

  const loadScans = useCallback(async () => {
    const s = await api.scans()
    setScans(s)
    return s
  }, [])

  // Initial: list scans and open the latest unless a scan was chosen (e.g. from History).
  useEffect(() => {
    loadScans().then(s => { if (scanId == null && s[0]) setScanId(s[0].id) }).catch(fail)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  // Load the chosen scan + its proposals.
  useEffect(() => {
    if (scanId == null) { setScan(null); setProposals([]); return }
    Promise.all([api.scan(scanId), api.proposals(scanId)])
      .then(([s, rows]) => { setScan(s); loadRows(rows) })
      .catch(fail)
  }, [scanId, fail, loadRows, setProposals])

  // Poll while running.
  useEffect(() => {
    if (!scan || scan.status !== 'running') return
    const t = setInterval(async () => {
      try {
        const s = await api.scan(scan.id)
        setScan(s)
        if (s.status !== 'running') {
          clearInterval(t)
          loadRows(await api.proposals(s.id))
          loadScans()
        }
      } catch (e) { fail(e) }
    }, 700)
    return () => clearInterval(t)
  }, [scan, fail, loadScans, loadRows])

  const startScan = async (inbox: string, limit: number | undefined, onlyNew: boolean) => {
    try {
      const s = await api.startScan(inbox || undefined, limit, onlyNew)
      setProposals([])
      setScan(s)
      setScanId(s.id)
      loadScans()
    } catch (e) { fail(e) }
  }

  return { scans, scan, loadScans, startScan }
}
