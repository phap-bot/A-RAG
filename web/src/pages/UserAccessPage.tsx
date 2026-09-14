import {
  CheckCircle2,
  ChevronRight,
  Eye,
  FilePenLine,
  Search,
  ShieldCheck,
  Trash2,
  UserRoundCog,
} from 'lucide-react'
import { startTransition, useDeferredValue, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import {
  getAdminUsers,
  getAdminWorkspaces,
  getAdminFeedback,
  getAdminFeedbackSummary,
  revokeAdminWorkspaceRole,
  setAdminWorkspaceRole,
  triageAdminFeedback,
  updateAdminUser,
} from '../api'
import { Button } from '../components/ui/Button'
import { Select } from '../components/ui/Select'
import { readSessionState, sessionStorageKey, writeSessionState } from '../sessionState'
import type {
  AdminUserRecord,
  AdminWorkspaceRecord,
  AdminFeedbackRecord,
  AdminFeedbackSummary,
  GlobalRole,
  ProjectRole,
  UiBootstrap,
} from '../types'

type UserAccessPageProps = {
  bootstrap: UiBootstrap
}

const projectRoles: ProjectRole[] = ['owner', 'editor', 'viewer']
const rolePermissionKeys: Record<ProjectRole, string[]> = {
  owner: ['readQa', 'write', 'deleteMove', 'ingestion'],
  editor: ['readQa', 'write', 'deleteMove', 'ingestion'],
  viewer: ['readQa', 'preview'],
}

type UserAccessSessionState = {
  selectedUserId: string
  query: string
  newWorkspaceId: string
  newProjectRole: ProjectRole
}

export function UserAccessPage({ bootstrap }: UserAccessPageProps) {
  const { t } = useTranslation()
  const sessionKey = sessionStorageKey('admin-access', bootstrap.session.email || bootstrap.session.display_name)
  const savedSession = readSessionState<UserAccessSessionState>(sessionKey)
  const [users, setUsers] = useState<AdminUserRecord[]>([])
  const [workspaces, setWorkspaces] = useState<AdminWorkspaceRecord[]>([])
  const [selectedUserId, setSelectedUserId] = useState(savedSession?.selectedUserId || '')
  const [query, setQuery] = useState(savedSession?.query || '')
  const [newWorkspaceId, setNewWorkspaceId] = useState(savedSession?.newWorkspaceId || '')
  const [newProjectRole, setNewProjectRole] = useState<ProjectRole>(savedSession?.newProjectRole || 'viewer')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState('')
  const [error, setError] = useState('')
  const [feedbackSummary, setFeedbackSummary] = useState<AdminFeedbackSummary | null>(null)
  const [feedback, setFeedback] = useState<AdminFeedbackRecord[]>([])
  const deferredQuery = useDeferredValue(query.trim().toLocaleLowerCase())

  useEffect(() => {
    writeSessionState<UserAccessSessionState>(sessionKey, {
      selectedUserId,
      query,
      newWorkspaceId,
      newProjectRole,
    })
  }, [newProjectRole, newWorkspaceId, query, selectedUserId, sessionKey])

  async function loadFeedbackData() {
    try {
      const [nextSummary, nextFeedback] = await Promise.all([
        getAdminFeedbackSummary(),
        getAdminFeedback(),
      ])
      setFeedbackSummary(nextSummary)
      setFeedback(nextFeedback)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('access.loadError'))
    }
  }

  async function loadAccessData(showLoading = true) {
    if (showLoading) setLoading(true)
    setError('')
    try {
      const [nextUsers, nextWorkspaces] = await Promise.all([
        getAdminUsers(),
        getAdminWorkspaces(),
      ])
      setUsers(nextUsers)
      setWorkspaces(nextWorkspaces)
      startTransition(() => {
        setSelectedUserId((current) => (
          nextUsers.some((user) => user.id === current) ? current : nextUsers[0]?.id || ''
        ))
      })
      void loadFeedbackData()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('access.loadError'))
    } finally {
      if (showLoading) setLoading(false)
    }
  }

  useEffect(() => {
    void loadAccessData()
  }, [])

  const filteredUsers = users.filter((user) => (
    !deferredQuery
    || `${user.display_name} ${user.email} ${user.role}`.toLocaleLowerCase().includes(deferredQuery)
  ))
  const selectedUser = users.find((user) => user.id === selectedUserId) || null
  const availableWorkspaces = workspaces.filter((workspace) => (
    !selectedUser?.memberships.some((membership) => membership.workspace_id === workspace.workspace_id)
  ))
  const isCurrentAccount = selectedUser?.email === bootstrap.session.email

  useEffect(() => {
    setNewWorkspaceId((current) => (
      availableWorkspaces.some((workspace) => workspace.workspace_id === current)
        ? current
        : availableWorkspaces[0]?.workspace_id || ''
    ))
  }, [selectedUserId, users, workspaces])

  async function mutate(key: string, action: () => Promise<unknown>) {
    setSaving(key)
    setError('')
    try {
      await action()
      await loadAccessData(false)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('access.saveError'))
    } finally {
      setSaving('')
    }
  }

  function changeGlobalRole(role: GlobalRole) {
    if (!selectedUser) return
    void mutate(`global:${selectedUser.id}`, () => updateAdminUser(selectedUser.id, { role }))
  }

  function toggleAccount() {
    if (!selectedUser) return
    void mutate(`active:${selectedUser.id}`, () => updateAdminUser(selectedUser.id, {
      is_active: !selectedUser.is_active,
    }))
  }

  function changeProjectRole(workspaceId: string, role: ProjectRole) {
    if (!selectedUser) return
    void mutate(`membership:${workspaceId}`, () => setAdminWorkspaceRole(selectedUser.id, workspaceId, role))
  }

  function addProjectAccess() {
    if (!selectedUser || !newWorkspaceId) return
    void mutate('membership:add', () => setAdminWorkspaceRole(selectedUser.id, newWorkspaceId, newProjectRole))
  }

  function removeProjectAccess(workspaceId: string) {
    if (!selectedUser) return
    if (!window.confirm(t('access.removeConfirm', { workspaceId, email: selectedUser.email }))) return
    void mutate(`membership:${workspaceId}`, () => revokeAdminWorkspaceRole(selectedUser.id, workspaceId))
  }

  async function updateFeedback(item: AdminFeedbackRecord, nextStatus: AdminFeedbackRecord['triage_status']) {
    setSaving(`feedback:${item.feedback_id}`)
    setError('')
    try {
      const updated = await triageAdminFeedback(item.feedback_id, {
        status: nextStatus,
        expected_version: item.version,
      })
      setFeedback((current) => current.map((entry) => entry.feedback_id === updated.feedback_id ? updated : entry))
      setFeedbackSummary((current) => current ? {
        ...current,
        unresolved_total: current.unresolved_total
          - ((item.triage_status === 'new' || item.triage_status === 'reviewing') ? 1 : 0)
          + ((nextStatus === 'new' || nextStatus === 'reviewing') ? 1 : 0),
      } : current)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('access.saveError'))
    } finally {
      setSaving('')
    }
  }

  if (loading) {
    return <section className="admin-page admin-loading" aria-live="polite">{t('access.loading')}</section>
  }

  return (
    <section className="admin-page user-access-page">
      <div className="admin-orb admin-orb--one" aria-hidden="true" />
      <div className="admin-orb admin-orb--two" aria-hidden="true" />
      <div className="admin-content">
        <header className="admin-hero access-hero">
          <div>
            <span className="admin-eyebrow"><UserRoundCog size={15} /> {t('access.eyebrow')}</span>
            <h1>{t('access.title')}</h1>
            <p>{t('access.description')}</p>
          </div>
          <div className="admin-identity">
            <span>{t('access.managedBy')}</span>
            <strong>{bootstrap.session.display_name}</strong>
            <small>{bootstrap.session.email}</small>
          </div>
        </header>

        {error && <div className="admin-alert" role="alert">{error}</div>}

        <div className="admin-control-grid access-control-grid">
          <aside className="admin-panel admin-directory">
            <div className="admin-panel-heading">
              <div><span>{t('access.directoryLabel')}</span><h2>{t('access.directoryTitle')}</h2></div>
              <b>{users.length}</b>
            </div>
            <label className="admin-search">
              <Search size={16} aria-hidden="true" />
              <span className="sr-only">{t('access.searchLabel')}</span>
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder={t('access.searchPlaceholder')} />
            </label>
            <div className="admin-user-list">
              {filteredUsers.map((user) => (
                <button
                  key={user.id}
                  type="button"
                  className={user.id === selectedUserId ? 'admin-user-row is-selected' : 'admin-user-row'}
                  onClick={() => setSelectedUserId(user.id)}
                >
                  <span className="admin-user-avatar">{user.display_name.slice(0, 2).toLocaleUpperCase()}</span>
                  <span className="admin-user-copy">
                    <strong>{user.display_name}</strong>
                    <small>{user.email}</small>
                    <em>{t('access.userMeta', { role: t(`role.${user.role}`), count: user.workspace_count })}</em>
                  </span>
                  <span
                    className={`admin-status-dot ${user.is_active ? 'is-active' : ''}`}
                    title={t(user.is_active ? 'common.active' : 'common.disabled')}
                  />
                  <ChevronRight size={16} aria-hidden="true" />
                </button>
              ))}
              {!filteredUsers.length && <div className="admin-empty">{t('access.noResults')}</div>}
            </div>
          </aside>

          <section className="admin-panel admin-user-detail">
            {selectedUser ? (
              <>
                <div className="admin-detail-head">
                  <div>
                    <span className="admin-kicker">{t('access.profileLabel')}</span>
                    <h2>{selectedUser.display_name}</h2>
                    <p>{selectedUser.email} {isCurrentAccount && <b>{t('access.currentAccount')}</b>}</p>
                  </div>
                  <span className={`admin-account-state ${selectedUser.is_active ? 'is-active' : ''}`}>
                    <CheckCircle2 size={15} /> {t(selectedUser.is_active ? 'common.active' : 'common.disabled')}
                  </span>
                </div>

                <div className="admin-global-access">
                  <label>
                    <span>{t('access.globalRole')}</span>
                    <Select
                      value={selectedUser.role}
                      ariaLabel={t('access.globalRole')}
                      options={[
                        { value: 'member', label: t('access.memberOption') },
                        { value: 'admin', label: t('access.adminOption') },
                      ]}
                      disabled={saving !== '' || Boolean(isCurrentAccount)}
                      onChange={(value) => changeGlobalRole(value as GlobalRole)}
                    />
                  </label>
                  <div>
                    <span>{t('access.accountStatus')}</span>
                    <Button
                      variant={selectedUser.is_active ? 'danger' : 'secondary'}
                      size="sm"
                      disabled={saving !== '' || Boolean(isCurrentAccount)}
                      loading={saving === `active:${selectedUser.id}`}
                      onClick={toggleAccount}
                    >
                      {t(selectedUser.is_active ? 'access.deactivate' : 'access.activate')}
                    </Button>
                  </div>
                </div>

                <div className="access-permission-note">
                  <ShieldCheck size={18} />
                  <div><strong>{t('access.serverPermissionTitle')}</strong><span>{t('access.serverPermissionBody')}</span></div>
                </div>

                <div className="admin-membership-head">
                  <div><span>{t('access.workspacePermissions')}</span><h3>{t('access.workspaceCount', { count: selectedUser.workspace_count })}</h3></div>
                </div>

                <div className="admin-memberships access-memberships">
                  {selectedUser.memberships.map((membership) => (
                    <div className="access-membership-card" key={membership.workspace_id}>
                      <div className="access-membership-main">
                        <div>
                          <strong>{workspaces.find((item) => item.workspace_id === membership.workspace_id)?.name || membership.workspace_id}</strong>
                          <small>{membership.workspace_id}</small>
                        </div>
                        <div className="access-membership-actions">
                          <Select
                            value={membership.role}
                            ariaLabel={t('access.roleAtWorkspace', { workspaceId: membership.workspace_id })}
                            options={projectRoles.map((role) => ({ value: role, label: t(`role.${role}`) }))}
                            disabled={saving !== ''}
                            onChange={(value) => changeProjectRole(membership.workspace_id, value as ProjectRole)}
                          />
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={t('access.removeWorkspace', { workspaceId: membership.workspace_id })}
                            title={t('access.removeWorkspaceTitle')}
                            disabled={saving !== ''}
                            loading={saving === `membership:${membership.workspace_id}`}
                            onClick={() => removeProjectAccess(membership.workspace_id)}
                          >
                            <Trash2 size={16} />
                          </Button>
                        </div>
                      </div>
                      <div className="access-capabilities">
                        {rolePermissionKeys[membership.role].map((permissionKey) => (
                          <span key={permissionKey}>
                            {membership.role === 'viewer' ? <Eye size={12} /> : <FilePenLine size={12} />}
                            {t(`access.permission.${permissionKey}`)}
                          </span>
                        ))}
                      </div>
                    </div>
                  ))}
                  {!selectedUser.memberships.length && <div className="admin-empty">{t('access.noMemberships')}</div>}
                </div>

                <div className="admin-add-access access-add-form">
                  <label>
                    <span>{t('access.addWorkspace')}</span>
                    <Select
                      value={newWorkspaceId}
                      ariaLabel={t('access.addWorkspace')}
                      options={availableWorkspaces.map((workspace) => ({ value: workspace.workspace_id, label: workspace.name }))}
                      onChange={setNewWorkspaceId}
                      disabled={!availableWorkspaces.length || saving !== ''}
                    />
                  </label>
                  <label>
                    <span>{t('access.projectRole')}</span>
                    <Select
                      value={newProjectRole}
                      ariaLabel={t('access.projectRole')}
                      options={projectRoles.map((role) => ({ value: role, label: t(`role.${role}`) }))}
                      onChange={(value) => setNewProjectRole(value as ProjectRole)}
                      disabled={saving !== ''}
                    />
                  </label>
                  <Button variant="primary" loading={saving === 'membership:add'} disabled={!newWorkspaceId || saving !== ''} onClick={addProjectAccess}>{t('access.grant')}</Button>
                </div>
              </>
            ) : <div className="admin-empty">{t('access.selectAccount')}</div>}
          </section>
        </div>
        {feedbackSummary && (
          <section className="admin-panel admin-feedback-monitor">
            <div className="admin-panel-heading"><div><span>Quality operations</span><h2>Answer feedback triage</h2></div><b>{feedbackSummary.unresolved_total} open</b></div>
            <div className="metric-grid admin-feedback-metrics">
              <article><span>Total feedback</span><strong>{feedbackSummary.feedback_total}</strong></article>
              <article><span>Negative</span><strong>{feedbackSummary.negative_total}</strong></article>
              <article><span>Unresolved</span><strong>{feedbackSummary.unresolved_total}</strong></article>
            </div>
            <div className="admin-feedback-list">
              {feedback.filter((item) => item.triage_status === 'new' || item.triage_status === 'reviewing').map((item) => (
                <article key={item.feedback_id} className="admin-feedback-row">
                  <div><strong>{item.rating === 'negative' ? 'Negative answer' : 'Positive answer'}</strong><small>{item.project_id} · {item.reason_codes.join(', ') || 'no reason'}</small>{item.comment && <p>{item.comment}</p>}</div>
                  <Select
                    value={item.triage_status}
                    ariaLabel="Feedback triage status"
                    options={[
                      { value: 'new', label: 'New' },
                      { value: 'reviewing', label: 'Reviewing' },
                      { value: 'resolved', label: 'Resolved' },
                      { value: 'dismissed', label: 'Dismissed' },
                    ]}
                    disabled={saving === `feedback:${item.feedback_id}`}
                    onChange={(value) => void updateFeedback(item, value as AdminFeedbackRecord['triage_status'])}
                  />
                </article>
              ))}
              {!feedback.some((item) => item.triage_status === 'new' || item.triage_status === 'reviewing') && <div className="admin-empty">No open feedback items.</div>}
            </div>
          </section>
        )}
      </div>
    </section>
  )
}
