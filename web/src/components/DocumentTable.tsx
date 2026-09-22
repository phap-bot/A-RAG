import { useEffect, useRef, useState } from 'react'
import {
  Bot,
  ChevronRight,
  Download,
  FileCode2,
  ScanSearch,
  FileText,
  Folder,
  FolderOpen,
  MoreHorizontal,
  Pencil,
  Trash2,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import type { DocumentRow } from '../types'
import { Button } from './ui/Button'

type DocumentTableProps = {
  documents: DocumentRow[]
  compact?: boolean
  selectedIds?: Set<string>
  onToggle?: (documentId: string) => void
  onToggleMany?: (documentIds: string[]) => void
  onToggleAll?: () => void
  onRename?: (document: DocumentRow) => void
  onDownload?: (document: DocumentRow) => void
  onAnalyze?: (document: DocumentRow) => void
  onOpen?: (document: DocumentRow) => void
  onDelete?: (document: DocumentRow) => void
}

type FolderNode = {
  key: string
  name: string
  folders: Map<string, FolderNode>
  documents: DocumentRow[]
}

type TreeRow =
  | { type: 'folder'; key: string; name: string; depth: number; documents: DocumentRow[] }
  | { type: 'document'; key: string; depth: number; document: DocumentRow }

type ActionMenuState = {
  document: DocumentRow
  top: number
  left: number
  placement: 'top' | 'bottom'
}

const localeMap: Record<string, string> = { en: 'en-US', ja: 'ja-JP', vi: 'vi-VN' }
const ACTION_MENU_WIDTH = 196
const ACTION_MENU_OFFSET = 8
const VIEWPORT_PADDING = 12

function normalizeDocumentStatus(status: string): string {
  const normalized = status.trim().toLowerCase().replace(/_/g, '-')
  if (normalized === 'uploaded') return 'uploaded'
  if (['pending', 'queued', 'waiting', 'discovered'].includes(normalized)) return 'pending'
  if ([
    'processing',
    'running',
    'classifying',
    'classified',
    'parsing',
    'parsed',
    'chunking',
    'chunked',
    'indexing',
  ].includes(normalized)) return 'processing'
  if (['indexed', 'skipped-unchanged'].includes(normalized)) return 'indexed'
  if (['failed', 'error'].includes(normalized)) return 'failed'
  return normalized || 'pending'
}

function documentStatusLabel(status: string, translate: (key: string) => string): string {
  const labels: Record<string, string> = {
    uploaded: translate('table.statusUploaded'),
    pending: translate('table.statusPending'),
    processing: translate('table.statusProcessing'),
    indexed: translate('table.statusIndexed'),
    failed: translate('table.statusFailed'),
  }
  return labels[status] || status
}

function displayDate(value: string, locale: string): string {
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleDateString(locale)
}

function documentPath(document: DocumentRow): { scope: string; folders: string[] } {
  const parts = document.sourcePath.replace(/\\/g, '/').split('/').filter(Boolean)
  const scope = parts[0] === 'uploads' ? 'uploads' : 'source'
  if (scope === 'uploads') parts.shift()
  return { scope, folders: parts.slice(0, -1) }
}

function collectFolderDocuments(folder: FolderNode): DocumentRow[] {
  return [
    ...folder.documents,
    ...Array.from(folder.folders.values()).flatMap(collectFolderDocuments),
  ]
}

function buildTreeRows(documents: DocumentRow[], collapsedFolders: Set<string>, locale: string): TreeRow[] {
  const root: FolderNode = { key: 'root', name: '', folders: new Map(), documents: [] }

  for (const document of documents) {
    const { scope, folders } = documentPath(document)
    let parent = root
    let path = `${document.workspace}:${scope}`
    for (const name of folders) {
      path = `${path}/${name}`
      let folder = parent.folders.get(path)
      if (!folder) {
        folder = { key: path, name, folders: new Map(), documents: [] }
        parent.folders.set(path, folder)
      }
      parent = folder
    }
    parent.documents.push(document)
  }

  const rows: TreeRow[] = []
  const appendFolder = (folder: FolderNode, depth: number) => {
    rows.push({
      type: 'folder',
      key: folder.key,
      name: folder.name,
      depth,
      documents: collectFolderDocuments(folder),
    })
    if (collapsedFolders.has(folder.key)) return
    Array.from(folder.folders.values())
      .sort((left, right) => left.name.localeCompare(right.name, locale))
      .forEach((child) => appendFolder(child, depth + 1))
    folder.documents.forEach((document) => {
      rows.push({ type: 'document', key: document.id, depth: depth + 1, document })
    })
  }

  Array.from(root.folders.values())
    .sort((left, right) => left.name.localeCompare(right.name, locale))
    .forEach((folder) => appendFolder(folder, 0))
  root.documents.forEach((document) => {
    rows.push({ type: 'document', key: document.id, depth: 0, document })
  })
  return rows
}

export function DocumentTable({
  documents,
  compact = false,
  selectedIds,
  onToggle,
  onToggleMany,
  onToggleAll,
  onRename,
  onDownload,
  onAnalyze,
  onOpen,
  onDelete,
}: DocumentTableProps) {
  const { t, i18n } = useTranslation()
  const [collapsedFolders, setCollapsedFolders] = useState<Set<string>>(new Set())
  const [actionMenu, setActionMenu] = useState<ActionMenuState | null>(null)
  const actionMenuRef = useRef<HTMLDivElement | null>(null)
  const actionButtonRefs = useRef(new Map<string, HTMLButtonElement>())
  const selectable = Boolean(selectedIds && onToggle)
  const hasActions = Boolean(onDownload || onAnalyze || onOpen || onRename || onDelete)
  const allSelected = selectable && documents.length > 0 && documents.every((document) => selectedIds?.has(document.id))
  const locale = localeMap[(i18n.resolvedLanguage || 'vi').split('-')[0]] || 'vi-VN'
  const rows = buildTreeRows(documents, collapsedFolders, locale)

  useEffect(() => {
    setActionMenu(null)
  }, [documents])

  useEffect(() => {
    if (!actionMenu) return

    const actionMenuDocumentId = actionMenu.document.id

    function closeMenu() {
      setActionMenu(null)
    }

    function handlePointerDown(event: MouseEvent) {
      const target = event.target as Node | null
      if (!target) return
      if (actionMenuRef.current?.contains(target)) return
      const trigger = actionButtonRefs.current.get(actionMenuDocumentId)
      if (trigger?.contains(target)) return
      closeMenu()
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        closeMenu()
        actionButtonRefs.current.get(actionMenuDocumentId)?.focus()
      }
    }

    window.addEventListener('mousedown', handlePointerDown)
    window.addEventListener('keydown', handleKeyDown)
    window.addEventListener('resize', closeMenu)
    window.addEventListener('scroll', closeMenu, true)
    return () => {
      window.removeEventListener('mousedown', handlePointerDown)
      window.removeEventListener('keydown', handleKeyDown)
      window.removeEventListener('resize', closeMenu)
      window.removeEventListener('scroll', closeMenu, true)
    }
  }, [actionMenu])

  function toggleFolder(folderKey: string) {
    setCollapsedFolders((current) => {
      const next = new Set(current)
      if (next.has(folderKey)) next.delete(folderKey)
      else next.add(folderKey)
      return next
    })
  }

  function setActionButtonRef(documentId: string, node: HTMLButtonElement | null) {
    if (node) {
      actionButtonRefs.current.set(documentId, node)
      return
    }
    actionButtonRefs.current.delete(documentId)
  }

  function openActionMenuFor(document: DocumentRow, trigger: HTMLButtonElement) {
    if (actionMenu?.document.id === document.id) {
      setActionMenu(null)
      return
    }

    const rect = trigger.getBoundingClientRect()
    const actionCount = [onDownload, onAnalyze, onOpen, onRename, onDelete].filter(Boolean).length
    const estimatedHeight = actionCount * 36 + 20
    const spaceBelow = window.innerHeight - rect.bottom - VIEWPORT_PADDING
    const spaceAbove = rect.top - VIEWPORT_PADDING
    const placement = spaceBelow < estimatedHeight && spaceAbove > spaceBelow ? 'top' : 'bottom'
    const left = Math.max(
      VIEWPORT_PADDING,
      Math.min(rect.right - ACTION_MENU_WIDTH, window.innerWidth - ACTION_MENU_WIDTH - VIEWPORT_PADDING),
    )
    const top = placement === 'top'
      ? Math.max(VIEWPORT_PADDING, rect.top - estimatedHeight - ACTION_MENU_OFFSET)
      : Math.min(window.innerHeight - estimatedHeight - VIEWPORT_PADDING, rect.bottom + ACTION_MENU_OFFSET)

    setActionMenu({ document, top, left, placement })
  }

  function handleMenuAction(callback: ((document: DocumentRow) => void) | undefined, document: DocumentRow) {
    if (!callback) return
    setActionMenu(null)
    callback(document)
  }

  return (
    <>
      <div className={`document-table${compact ? ' is-compact' : ''}${selectable ? ' is-selectable' : ''}`} role="table" aria-label={t('table.label')}>
        <div className="document-row document-head" role="row">
          {selectable && (
            <label className="document-check">
              <input type="checkbox" checked={allSelected} onChange={() => onToggleAll?.()} aria-label={t('table.selectAll')} />
              <span />
            </label>
          )}
          <span role="columnheader">{t('table.name')}</span>
          <span role="columnheader">{t('table.status')}</span>
          <span role="columnheader">{t('table.pages')}</span>
          <span role="columnheader">{t('table.updated')}</span>
          <span role="columnheader" aria-label={t('table.actions')} />
        </div>
        <div className="document-body">
          {rows.length ? rows.map((row) => {
            if (row.type === 'folder') {
              const isCollapsed = collapsedFolders.has(row.key)
              const folderSelected = row.documents.length > 0 && row.documents.every((document) => selectedIds?.has(document.id))
              const latestUpdate = row.documents.reduce(
                (latest, document) => document.uploadedAt > latest ? document.uploadedAt : latest,
                '',
              )
              return (
                <div className="document-row document-folder-row" role="row" key={row.key}>
                  {selectable && (
                    <label className="document-check">
                      <input
                        type="checkbox"
                        checked={folderSelected}
                        onChange={() => onToggleMany?.(row.documents.map((document) => document.id))}
                        aria-label={t('table.selectFolder', { name: row.name })}
                      />
                      <span />
                    </label>
                  )}
                  <button
                    className="document-folder"
                    type="button"
                    onClick={() => toggleFolder(row.key)}
                    aria-expanded={!isCollapsed}
                    style={{ paddingInlineStart: `${row.depth * 18}px` }}
                  >
                    <ChevronRight className="folder-chevron" aria-hidden="true" size={15} />
                    <i aria-hidden="true">{isCollapsed ? <Folder size={18} /> : <FolderOpen size={18} />}</i>
                    <b>{row.name}</b>
                  </button>
                  <span role="cell" className="folder-file-count">{t('table.fileCount', { count: row.documents.length })}</span>
                  <span role="cell">-</span>
                  <span role="cell">{latestUpdate ? displayDate(latestUpdate, locale) : '-'}</span>
                  <span />
                </div>
              )
            }

            const { document } = row
            const normalizedStatus = normalizeDocumentStatus(document.status)
            return (
              <div className="document-row document-file-row" role="row" key={row.key}>
                {selectable && (
                  <label className="document-check">
                    <input
                      type="checkbox"
                      checked={selectedIds?.has(document.id) ?? false}
                      onChange={() => onToggle?.(document.id)}
                      aria-label={t('table.selectDocument', { name: document.name })}
                    />
                    <span />
                  </label>
                )}
                <span role="cell" className="document-name-cell">
                  <button className="document-name document-name-button" type="button" title={document.sourcePath} style={{ paddingInlineStart: `${row.depth * 18}px` }} onClick={() => onOpen?.(document)}>
                    <i aria-hidden="true">
                      {document.contentType.includes('json') || document.contentType.includes('xml')
                        ? <FileCode2 size={17} />
                        : <FileText size={17} />}
                    </i>
                    <b>{document.name}</b>
                  </button>
                </span>
                <span role="cell"><i className="status-indicator" data-status={normalizedStatus} />{documentStatusLabel(normalizedStatus, t)}</span>
                <span role="cell">{/\d/.test(document.page) ? document.page : '-'}</span>
                <span role="cell">{displayDate(document.uploadedAt, locale)}</span>
                {hasActions ? (
                  <div className="row-action">
                    <button
                      ref={(node) => setActionButtonRef(document.id, node)}
                      className="row-action-trigger"
                      type="button"
                      aria-haspopup="menu"
                      aria-expanded={actionMenu?.document.id === document.id}
                      aria-label={t('table.actionsFor', { name: document.name })}
                      onClick={(event) => openActionMenuFor(document, event.currentTarget)}
                    >
                      <MoreHorizontal aria-hidden="true" size={18} />
                    </button>
                  </div>
                ) : <span />}
              </div>
            )
          }) : (
            <div className="table-empty">
              <strong>{t('table.emptyTitle')}</strong>
              <span>{t('table.emptyBody')}</span>
            </div>
          )}
        </div>
      </div>

      {actionMenu && (
        <div
          ref={actionMenuRef}
          className={`row-action-menu row-action-menu--${actionMenu.placement}`}
          role="menu"
          aria-label={t('table.actionsFor', { name: actionMenu.document.name })}
          style={{ top: actionMenu.top, left: actionMenu.left }}
        >
          {onDownload && (
            <Button variant="ghost" size="sm" leadingIcon={<Download aria-hidden="true" />} role="menuitem" onClick={() => handleMenuAction(onDownload, actionMenu.document)}>
              {t('table.download')}
            </Button>
          )}
          {onAnalyze && (
            <Button variant="ghost" size="sm" leadingIcon={<Bot aria-hidden="true" />} role="menuitem" onClick={() => handleMenuAction(onAnalyze, actionMenu.document)}>
              {t('table.analyze')}
            </Button>
          )}
          {onOpen && (
            <Button variant="ghost" size="sm" leadingIcon={<ScanSearch aria-hidden="true" />} role="menuitem" onClick={() => handleMenuAction(onOpen, actionMenu.document)}>
              {t('table.review')}
            </Button>
          )}
          {onRename && (
            <Button variant="ghost" size="sm" leadingIcon={<Pencil aria-hidden="true" />} role="menuitem" disabled={!actionMenu.document.editable} onClick={() => handleMenuAction(onRename, actionMenu.document)}>
              {t('table.rename')}
            </Button>
          )}
          {onDelete && (
            <Button variant="ghost" size="sm" leadingIcon={<Trash2 aria-hidden="true" />} role="menuitem" disabled={!actionMenu.document.editable} onClick={() => handleMenuAction(onDelete, actionMenu.document)}>
              {t('table.delete')}
            </Button>
          )}
        </div>
      )}
    </>
  )
}
