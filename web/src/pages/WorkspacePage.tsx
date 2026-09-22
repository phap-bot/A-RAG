import { FormEvent, useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Archive,
  ArrowUpRight,
  FileText,
  FolderKanban,
  LayoutDashboard,
  Plus,
  Search,
  Sparkles,
  Trash2,
  TriangleAlert,
  X,
} from 'lucide-react'

import { Button } from '../components/ui/Button'
import type { UiBootstrap, WorkspaceRecord } from '../types'

type WorkspacePageProps = {
  bootstrap: UiBootstrap
  workspaces: WorkspaceRecord[]
  onSearch: (query: string) => Promise<void>
  onCreate: (name: string) => Promise<WorkspaceRecord>
  onDelete: (workspace: WorkspaceRecord, confirmation: string) => Promise<void>
  onSelect: (workspace: WorkspaceRecord) => void
  onOpenAssistant: () => void
}

const localeMap: Record<string, string> = { en: 'en-US', ja: 'ja-JP', vi: 'vi-VN' }

export function WorkspacePage({
  bootstrap,
  workspaces,
  onSearch,
  onCreate,
  onDelete,
  onSelect,
  onOpenAssistant,
}: WorkspacePageProps) {
  const { t, i18n } = useTranslation()
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [creating, setCreating] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [createName, setCreateName] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<WorkspaceRecord | null>(null)
  const [deleteConfirmation, setDeleteConfirmation] = useState('')
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [railCollapsed, setRailCollapsed] = useState(false)
  const createInputRef = useRef<HTMLInputElement>(null)
  const deleteInputRef = useRef<HTMLInputElement>(null)
  const locale = localeMap[(i18n.resolvedLanguage || 'vi').split('-')[0]] || 'vi-VN'
  const normalizedQuery = query.trim().toLocaleLowerCase(locale)
  const visibleWorkspaces = normalizedQuery
    ? workspaces.filter((item) => (
      `${item.name} ${item.workspace_id}`.toLocaleLowerCase(locale).includes(normalizedQuery)
    ))
    : workspaces

  useEffect(() => {
    if (!createOpen) return
    const frame = window.requestAnimationFrame(() => {
      createInputRef.current?.focus()
      createInputRef.current?.select()
    })

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setCreateOpen(false)
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.cancelAnimationFrame(frame)
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [createOpen])

  useEffect(() => {
    if (!deleteTarget) return
    const frame = window.requestAnimationFrame(() => {
      deleteInputRef.current?.focus()
    })

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === 'Escape' && !deleting) {
        setDeleteTarget(null)
        setDeleteConfirmation('')
        setDeleteError(null)
      }
    }

    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.cancelAnimationFrame(frame)
      window.removeEventListener('keydown', onKeyDown)
    }
  }, [deleteTarget, deleting])

  function openCreateDialog() {
    setError(null)
    setCreateName('')
    setCreateOpen(true)
  }

  function closeCreateDialog() {
    if (creating) return
    setCreateOpen(false)
    setCreateName('')
  }

  function openDeleteDialog(workspace: WorkspaceRecord) {
    setDeleteTarget(workspace)
    setDeleteConfirmation('')
    setDeleteError(null)
  }

  function closeDeleteDialog() {
    if (deleting) return
    setDeleteTarget(null)
    setDeleteConfirmation('')
    setDeleteError(null)
  }

  async function submitSearch(event: FormEvent) {
    event.preventDefault()
    setSearching(true)
    setError(null)
    try {
      await onSearch(query)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('workspace.searchError'))
    } finally {
      setSearching(false)
    }
  }

  async function submitCreate(event: FormEvent) {
    event.preventDefault()
    const name = createName.trim()
    if (!name) {
      setError(t('workspace.createValidation'))
      return
    }

    setCreating(true)
    setError(null)
    try {
      await onCreate(name)
      setCreateOpen(false)
      setCreateName('')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('workspace.createError'))
    } finally {
      setCreating(false)
    }
  }

  async function submitDelete(event: FormEvent) {
    event.preventDefault()
    if (!deleteTarget || deleteConfirmation !== deleteTarget.workspace_id) {
      setDeleteError(t('workspace.deleteValidation'))
      return
    }

    setDeleting(true)
    setDeleteError(null)
    try {
      await onDelete(deleteTarget, deleteConfirmation)
      setDeleteTarget(null)
      setDeleteConfirmation('')
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : ''
      setDeleteError(
        message.includes('Wait for document processing')
          || message.includes('Wait for active questions')
          ? t('workspace.deleteActiveError')
          : message || t('workspace.deleteError'),
      )
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="page page-workspaces">
      <aside
        className="workspace-deck"
        data-rail-collapsed={railCollapsed ? 'true' : undefined}
        onMouseEnter={() => setRailCollapsed(false)}
        onMouseLeave={() => setRailCollapsed(true)}
        onClickCapture={() => setRailCollapsed(true)}
      >
        <div className="deck-card">
          <i><FolderKanban size={18} /></i>
          <div><strong>{t('workspace.deckTitle')}</strong><span>{bootstrap.brand.product}</span></div>
        </div>
        <Button variant="primary" fullWidth aria-label={t('workspace.new')} leadingIcon={<Plus size={17} />} loading={creating} onClick={openCreateDialog}>
          <span className="rail-label">{t('workspace.new')}</span>
        </Button>
        <nav aria-label={t('workspace.filtersLabel')}>
          <Button variant="ghost" aria-label={t('workspace.all')} className="is-active" leadingIcon={<LayoutDashboard size={17} />} onClick={() => void onSearch('')}><span className="rail-label">{t('workspace.all')}</span></Button>
          <Button variant="ghost" aria-label={t('workspace.searchResults')} leadingIcon={<FileText size={17} />} onClick={() => void onSearch(query)}><span className="rail-label">{t('workspace.searchResults')}</span></Button>
          <span className="deck-divider" />
          <div className="deck-info"><Archive size={16} /><span className="rail-label">{t('workspace.managedData')}</span></div>
        </nav>
        <div className="deck-security">
          <span />
          <div><strong>{t('workspace.footerSecurity')}</strong><small>{t('workspace.securityLabel')}</small></div>
        </div>
      </aside>

      <section className="workspace-dashboard" aria-labelledby="workspace-title">
        <div className="workspace-dashboard-heading">
          <div>
            <span>{t('workspace.directory')}</span>
            <h1 id="workspace-title">{t('workspace.welcome')}</h1>
            <p>{t('workspace.description')}</p>
          </div>
          <form className="workspace-query" onSubmit={submitSearch}>
            <Search size={17} aria-hidden="true" />
            <label className="sr-only" htmlFor="workspace-query">{t('workspace.searchLabel')}</label>
            <input id="workspace-query" value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t('workspace.searchPlaceholder')} />
            <Button variant="primary" size="sm" type="submit" loading={searching}>{t('workspace.searchAction')}</Button>
          </form>
        </div>

        {error && <p className="page-error" role="alert">{error}</p>}

        <div className="workspace-node-grid">
          {visibleWorkspaces.map((item, index) => (
            <article className="workspace-node" key={item.workspace_id}>
              <Button variant="unstyled" className="workspace-node-main" onClick={() => onSelect(item)} aria-label={`${item.name}, ${t('workspace.openAction')}`}>
                <div className="node-topline">
                  <i className={`node-icon node-icon--${(index % 3) + 1}`}><FolderKanban size={22} /></i>
                  <span data-status={item.status}>{item.status === 'ready' ? t('workspace.statusOnline') : item.status}</span>
                </div>
                <strong>{item.name}</strong>
                <div className="workspace-node-key">
                  <span>{t('workspace.keyLabel')}</span>
                  <code>{item.workspace_id}</code>
                </div>
                <p>{item.description}</p>
                <small>{t('workspace.documentCount', { count: item.document_count, formattedCount: item.document_count.toLocaleString(locale) })} <ArrowUpRight size={13} /></small>
              </Button>
              {item.access_role === 'owner' && (
                <Button
                  variant="unstyled"
                  size="icon"
                  className="workspace-node-delete"
                  aria-label={t('workspace.deleteActionLabel', { name: item.name })}
                  title={t('workspace.deleteAction')}
                  leadingIcon={<Trash2 size={15} />}
                  onClick={() => openDeleteDialog(item)}
                />
              )}
            </article>
          ))}

          <Button variant="unstyled" className="workspace-node workspace-node--create" onClick={openCreateDialog}>
            <i><Plus size={24} /></i>
            <strong>{t('workspace.deployNode')}</strong>
            <span>{t('workspace.deployHint')}</span>
          </Button>
        </div>

        {!visibleWorkspaces.length && !searching && (
          <div className="workspace-empty">
            <FolderKanban size={30} />
            <strong>{t('workspace.emptyTitle')}</strong>
            <span>{t('workspace.emptyBody')}</span>
          </div>
        )}
      </section>

      <Button variant="primary" size="icon" className="workspace-ai-fab" aria-label={t('workspace.openAssistant')} leadingIcon={<Sparkles size={20} />} disabled={!workspaces.length} onClick={onOpenAssistant} />

      <footer className="workspace-footer">
        <div><strong>{bootstrap.brand.name}</strong><span>{bootstrap.brand.product}</span></div>
        <span>{t('workspace.footerSecurity')}</span>
      </footer>

      {createOpen && (
        <div className="workspace-create-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) closeCreateDialog()
        }}>
          <div className="workspace-create-dialog" role="dialog" aria-modal="true" aria-labelledby="workspace-create-title" aria-describedby="workspace-create-description">
            <button type="button" className="workspace-create-close" aria-label={t('common.close')} onClick={closeCreateDialog}>
              <X size={18} />
            </button>
            <div className="workspace-create-header">
              <span className="workspace-create-eyebrow">{t('workspace.new')}</span>
              <h2 id="workspace-create-title">{t('workspace.createTitle')}</h2>
              <p id="workspace-create-description">{t('workspace.createDescription')}</p>
            </div>
            <form className="workspace-create-form" onSubmit={submitCreate}>
              <label htmlFor="workspace-create-name">{t('workspace.createLabel')}</label>
              <input
                ref={createInputRef}
                id="workspace-create-name"
                value={createName}
                onChange={(event) => setCreateName(event.target.value)}
                placeholder={t('workspace.createPlaceholder')}
                autoComplete="off"
                spellCheck={false}
              />
              <div className="workspace-create-actions">
                <Button variant="secondary" type="button" onClick={closeCreateDialog} disabled={creating}>{t('workspace.createCancel')}</Button>
                <Button variant="primary" type="submit" loading={creating}>{t('workspace.createConfirm')}</Button>
              </div>
            </form>
          </div>
        </div>
      )}

      {deleteTarget && (
        <div className="workspace-create-backdrop delete-confirm-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget) closeDeleteDialog()
        }}>
          <form className="delete-confirm-dialog workspace-delete-dialog" role="dialog" aria-modal="true" aria-labelledby="workspace-delete-title" aria-describedby="workspace-delete-description" onSubmit={submitDelete}>
            <div className="delete-confirm-icon"><TriangleAlert size={24} /></div>
            <div className="delete-confirm-copy">
              <span>{t('workspace.deleteEyebrow')}</span>
              <h2 id="workspace-delete-title">{t('workspace.deleteTitle', { name: deleteTarget.name })}</h2>
              <p id="workspace-delete-description">{t('workspace.deleteDescription')}</p>
            </div>
            <div className="workspace-delete-impact">
              <Trash2 size={16} />
              <p>{t('workspace.deleteImpact')}</p>
            </div>
            <label className="workspace-delete-field" htmlFor="workspace-delete-confirmation">
              <span>{t('workspace.deleteLabel')}</span>
              <small>{t('workspace.deleteHint')} <code>{deleteTarget.workspace_id}</code></small>
              <input
                ref={deleteInputRef}
                id="workspace-delete-confirmation"
                value={deleteConfirmation}
                onChange={(event) => {
                  setDeleteConfirmation(event.target.value)
                  setDeleteError(null)
                }}
                placeholder={deleteTarget.workspace_id}
                autoComplete="off"
                spellCheck={false}
                disabled={deleting}
              />
            </label>
            {deleteError && <p className="workspace-delete-error" role="alert">{deleteError}</p>}
            <div className="delete-confirm-actions">
              <Button variant="secondary" type="button" onClick={closeDeleteDialog} disabled={deleting}>{t('workspace.deleteCancel')}</Button>
              <Button variant="danger" type="submit" loading={deleting} disabled={deleteConfirmation !== deleteTarget.workspace_id}>{t('workspace.deleteConfirm')}</Button>
            </div>
          </form>
        </div>
      )}
    </div>
  )
}
