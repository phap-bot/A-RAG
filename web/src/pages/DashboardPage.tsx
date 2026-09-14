import {
  Activity,
  BarChart3,
  Boxes,
  CheckCircle2,
  Database,
  ShieldCheck,
  Users,
} from 'lucide-react'
import { useEffect, useState, type CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'

import { getAdminOverview, getAdminUsers, getAdminWorkspaces } from '../api'
import type {
  AdminOverview,
  AdminUserRecord,
  AdminWorkspaceRecord,
  UiBootstrap,
  WorkspaceRecord,
} from '../types'

type DashboardPageProps = {
  bootstrap: UiBootstrap
  workspaces: WorkspaceRecord[]
  isAdmin: boolean
}

const localeMap: Record<string, string> = { vi: 'vi-VN', en: 'en-US', ja: 'ja-JP' }

export function DashboardPage({ bootstrap, workspaces, isAdmin }: DashboardPageProps) {
  const { t, i18n } = useTranslation()
  const [overview, setOverview] = useState<AdminOverview | null>(null)
  const [users, setUsers] = useState<AdminUserRecord[]>([])
  const [adminWorkspaces, setAdminWorkspaces] = useState<AdminWorkspaceRecord[]>([])
  const [loading, setLoading] = useState(isAdmin)
  const [error, setError] = useState('')
  const locale = localeMap[(i18n.resolvedLanguage || 'vi').split('-')[0]] || 'vi-VN'
  const formatNumber = (value: number) => value.toLocaleString(locale)

  useEffect(() => {
    if (!isAdmin) return
    let active = true
    async function loadAnalytics() {
      try {
        const [nextOverview, nextUsers, nextWorkspaces] = await Promise.all([
          getAdminOverview(),
          getAdminUsers(),
          getAdminWorkspaces(),
        ])
        if (!active) return
        setOverview(nextOverview)
        setUsers(nextUsers)
        setAdminWorkspaces(nextWorkspaces)
      } catch (reason) {
        if (active) setError(reason instanceof Error ? reason.message : t('dashboard.loadError'))
      } finally {
        if (active) setLoading(false)
      }
    }
    void loadAnalytics()
    return () => { active = false }
  }, [isAdmin, t])

  const visibleWorkspaces = isAdmin ? adminWorkspaces : workspaces
  const totalDocuments = visibleWorkspaces.reduce((sum, item) => sum + item.document_count, 0)
  const readyWorkspaces = visibleWorkspaces.filter((item) => item.status === 'ready').length
  const activeUsers = overview?.active_users || 0
  const activeRate = overview?.total_users ? Math.round((activeUsers / overview.total_users) * 100) : 0
  const usersWithWorkspace = users.filter((user) => user.workspace_count > 0).length
  const accessCoverage = users.length ? Math.round((usersWithWorkspace / users.length) * 100) : 0
  const unassignedUsers = users.length - usersWithWorkspace
  const roleCounts = adminWorkspaces.reduce(
    (totals, workspace) => ({
      owner: totals.owner + workspace.role_counts.owner,
      editor: totals.editor + workspace.role_counts.editor,
      viewer: totals.viewer + workspace.role_counts.viewer,
    }),
    { owner: 0, editor: 0, viewer: 0 },
  )
  const maxRoleCount = Math.max(roleCounts.owner, roleCounts.editor, roleCounts.viewer, 1)
  const topWorkspaces = [...visibleWorkspaces]
    .sort((left, right) => right.document_count - left.document_count)
    .slice(0, 6)
  const maxDocuments = Math.max(...topWorkspaces.map((workspace) => workspace.document_count), 1)

  const metrics = isAdmin
    ? [
        { label: t('dashboard.metric.totalAccounts'), value: overview?.total_users || 0, detail: t('dashboard.metric.globalAdmins', { count: overview?.global_admins || 0 }), icon: Users },
        { label: t('dashboard.metric.activeUsers'), value: activeUsers, detail: t('dashboard.metric.activeRate', { percent: activeRate }), icon: CheckCircle2 },
        { label: t('dashboard.metric.systemWorkspaces'), value: overview?.total_workspaces || 0, detail: t('dashboard.metric.readyWorkspaces', { count: readyWorkspaces }), icon: Boxes },
        { label: t('dashboard.metric.totalDocuments'), value: totalDocuments, detail: t('dashboard.metric.memberships', { count: overview?.total_memberships || 0 }), icon: Database },
      ]
    : [
        { label: t('dashboard.metric.yourWorkspaces'), value: workspaces.length, detail: t('dashboard.metric.readyWorkspaces', { count: readyWorkspaces }), icon: Boxes },
        { label: t('dashboard.metric.totalDocuments'), value: totalDocuments, detail: t('dashboard.metric.scopeDocuments'), icon: Database },
        { label: t('dashboard.metric.activeWorkspaces'), value: readyWorkspaces, detail: t('dashboard.metric.backendDetected'), icon: CheckCircle2 },
        { label: t('dashboard.metric.accessRights'), value: workspaces.length, detail: t('dashboard.metric.serverEnforced'), icon: ShieldCheck },
      ]

  return (
    <section className="analytics-page">
      <div className="analytics-grid-bg" aria-hidden="true" />
      <div className="analytics-content">
        <header className="analytics-hero">
          <div>
            <span><Activity size={15} /> {t('dashboard.eyebrow')}</span>
            <h1>{t('dashboard.title')}</h1>
            <p>{t('dashboard.description')}</p>
          </div>
          <div className="analytics-context">
            <small>{t('dashboard.contextLabel')}</small>
            <strong>{t(isAdmin ? 'dashboard.contextAdmin' : 'dashboard.contextMember')}</strong>
            <span>{bootstrap.session.email}</span>
          </div>
        </header>

        {error && <div className="admin-alert" role="alert">{error}</div>}
        {loading ? (
          <div className="analytics-loading" aria-live="polite">{t('dashboard.loading')}</div>
        ) : (
          <>
            <div className="analytics-metrics">
              {metrics.map(({ label, value, detail, icon: Icon }) => (
                <article key={label}>
                  <div className="analytics-metric-top"><span>{label}</span><Icon size={17} /></div>
                  <strong>{formatNumber(value)}</strong>
                  <small>{detail}</small>
                </article>
              ))}
            </div>

            <div className="analytics-layout">
              <section className="analytics-card analytics-workspace-usage">
                <div className="analytics-card-head">
                  <div><span>{t('dashboard.dataFootprint')}</span><h2>{t('dashboard.workspaceUsage')}</h2></div>
                  <BarChart3 size={19} />
                </div>
                <div className="analytics-bars">
                  {topWorkspaces.map((workspace) => (
                    <div className="analytics-bar-row" key={workspace.workspace_id}>
                      <div><strong>{workspace.name}</strong><span>{t('dashboard.documentCount', { count: workspace.document_count, formattedCount: formatNumber(workspace.document_count) })}</span></div>
                      <div className="analytics-track"><i style={{ width: `${Math.max(4, (workspace.document_count / maxDocuments) * 100)}%` }} /></div>
                    </div>
                  ))}
                  {!topWorkspaces.length && <div className="admin-empty">{t('dashboard.noWorkspaceData')}</div>}
                </div>
              </section>

              <section className="analytics-card analytics-health">
                <div className="analytics-card-head">
                  <div><span>{t('dashboard.accountHealth')}</span><h2>{t('dashboard.accessStatus')}</h2></div>
                  <ShieldCheck size={19} />
                </div>
                <div className="analytics-donut-wrap">
                  <div className="analytics-donut" style={{ '--analytics-rate': `${isAdmin ? activeRate : 100}%` } as CSSProperties}>
                    <div><strong>{isAdmin ? activeRate : 100}%</strong><span>{t('dashboard.activeLabel')}</span></div>
                  </div>
                  <div className="analytics-health-copy">
                    <div><span>{t('dashboard.activeAccounts')}</span><strong>{isAdmin ? activeUsers : 1}</strong></div>
                    <div><span>{t('dashboard.accessCoverage')}</span><strong>{isAdmin ? `${accessCoverage}%` : '100%'}</strong></div>
                    <div><span>{t('dashboard.unassignedUsers')}</span><strong>{isAdmin ? unassignedUsers : 0}</strong></div>
                  </div>
                </div>
              </section>

              {isAdmin && (
                <section className="analytics-card analytics-role-distribution">
                  <div className="analytics-card-head">
                    <div><span>{t('dashboard.authorization')}</span><h2>{t('dashboard.roleDistribution')}</h2></div>
                    <Users size={19} />
                  </div>
                  <div className="analytics-role-rows">
                    {(['owner', 'editor', 'viewer'] as const).map((role) => (
                      <div key={role}>
                        <header><span>{t(`role.${role}`)}</span><strong>{roleCounts[role]}</strong></header>
                        <div><i data-role={role} style={{ width: `${(roleCounts[role] / maxRoleCount) * 100}%` }} /></div>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              <section className="analytics-card analytics-insights">
                <div className="analytics-card-head">
                  <div><span>{t('dashboard.operationalSignals')}</span><h2>{t('dashboard.attentionPoints')}</h2></div>
                  <Activity size={19} />
                </div>
                <div className="analytics-insight-list">
                  <article><span data-tone="good" /><div><strong>{t('dashboard.readySignal', { ready: readyWorkspaces, total: visibleWorkspaces.length })}</strong><small>{t('dashboard.readySignalBody')}</small></div></article>
                  {isAdmin && <article><span data-tone={unassignedUsers ? 'warn' : 'good'} /><div><strong>{t('dashboard.unassignedSignal', { count: unassignedUsers })}</strong><small>{t('dashboard.unassignedSignalBody')}</small></div></article>}
                  <article><span data-tone="info" /><div><strong>{t('dashboard.documentsSignal', { count: totalDocuments, formattedCount: formatNumber(totalDocuments) })}</strong><small>{t('dashboard.documentsSignalBody', { count: visibleWorkspaces.length })}</small></div></article>
                </div>
              </section>
            </div>
          </>
        )}
      </div>
    </section>
  )
}