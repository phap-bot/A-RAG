import { FormEvent, useEffect, useState } from 'react'
import {
  Check,
  Clipboard,
  Clock3,
  KeyRound,
  Link2,
  LockKeyhole,
  PlugZap,
  RotateCcw,
  ServerCog,
  ShieldCheck,
  Trash2,
  X,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import {
  getAdminUsers,
  getMcpCredentials,
  issueAdminMcpCredential,
  issueMcpCredential,
  revokeMcpCredential,
} from '../api'
import { Button } from '../components/ui/Button'
import { Select } from '../components/ui/Select'
import type {
  AdminUserRecord,
  IssuedMcpCredential,
  McpCredential,
  UiBootstrap,
  WorkspaceRecord,
} from '../types'

type AgentConnectionsPageProps = {
  bootstrap: UiBootstrap
  workspaces: WorkspaceRecord[]
  workspaceId?: string
  embedded?: boolean
}

type CredentialAudience = 'admin' | 'user'
type IssuedMcpOwner = {
  user_id: string
  display_name: string
  email: string
  global_role: string
}

function formatDate(value: string, locale: string): string {
  return new Intl.DateTimeFormat(locale, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
}

function resolvedConfig(issued: IssuedMcpCredential): string {
  const placeholder = '${' + issued.connection.api_key_env + '}'
  const config = JSON.stringify(issued.connection.config, null, 2)
  return config.split(placeholder).join(issued.api_key)
}

function sdkIntegrationJson(issued: IssuedMcpCredential): string {
  return JSON.stringify(issued.sdk_config, null, 2)
}

function pythonSdkSnippet(issued: IssuedMcpCredential): string {
  const { base_url, api_key, project_id } = issued.sdk_config
  const { python_module: moduleName, client_class: clientClass } = issued.connection.sdk
  return `import asyncio\n\nfrom ${moduleName} import ${clientClass}\n\n\nasync def main():\n    async with ${clientClass}(\n        base_url="${base_url}",\n        api_key="${api_key}",\n    ) as kb:\n        tools = await kb.list_tools()\n        projects = await kb.list_accessible_projects()\n        print(tools)\n        print("project_id", "${project_id}")\n        print(projects)\n\n\nasyncio.run(main())`
}

export function AgentConnectionsPage({
  bootstrap,
  workspaces,
  workspaceId: embeddedWorkspaceId,
  embedded = false,
}: AgentConnectionsPageProps) {
  const { t, i18n } = useTranslation()
  const [credentials, setCredentials] = useState<McpCredential[]>([])
  const [adminUsers, setAdminUsers] = useState<AdminUserRecord[]>([])
  const [workspaceId, setWorkspaceId] = useState(embeddedWorkspaceId || workspaces[0]?.workspace_id || '')
  const [name, setName] = useState('Local knowledge Agent')
  const [ttlDays, setTtlDays] = useState(30)
  const [credentialAudience, setCredentialAudience] = useState<CredentialAudience>('admin')
  const [targetUserId, setTargetUserId] = useState('')
  const [issued, setIssued] = useState<IssuedMcpCredential | null>(null)
  const [issuedOwner, setIssuedOwner] = useState<IssuedMcpOwner | null>(null)
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [copied, setCopied] = useState<'key' | 'config' | 'json' | 'python' | ''>('')
  const locale = i18n.resolvedLanguage || bootstrap.locale || 'vi'
  const isGlobalAdmin = bootstrap.session.role === 'admin'
  const eligibleTargetUsers = adminUsers.filter((user) => (
    user.role === 'member'
    && user.is_active
  ))

  async function loadCredentials() {
    setLoading(true)
    setError('')
    try {
      const [records, nextAdminUsers] = await Promise.all([
        getMcpCredentials(),
        isGlobalAdmin ? getAdminUsers() : Promise.resolve([] as AdminUserRecord[]),
      ])
      if (isGlobalAdmin) setAdminUsers(nextAdminUsers)
      setCredentials(embeddedWorkspaceId
        ? records.filter((credential) => credential.project_ids.includes(embeddedWorkspaceId))
        : records)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('mcp.loadError'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadCredentials()
  }, [embeddedWorkspaceId, isGlobalAdmin])

  useEffect(() => {
    if (embeddedWorkspaceId) {
      setWorkspaceId(embeddedWorkspaceId)
    } else if (!workspaces.some((workspace) => workspace.workspace_id === workspaceId)) {
      setWorkspaceId(workspaces[0]?.workspace_id || '')
    }
  }, [embeddedWorkspaceId, workspaceId, workspaces])

  useEffect(() => {
    if (!isGlobalAdmin) return
    const eligibleUsers = adminUsers.filter((user) => (
      user.role === 'member'
      && user.is_active
    ))
    const currentAccount = adminUsers.find((user) => user.email === bootstrap.session.email)
    setTargetUserId((current) => (
      eligibleUsers.some((user) => user.id === current)
        ? current
        : currentAccount && eligibleUsers.some((user) => user.id === currentAccount.id)
          ? currentAccount.id
          : eligibleUsers[0]?.id || ''
    ))
  }, [adminUsers, bootstrap.session.email, isGlobalAdmin, workspaceId])

  async function createCredential(event: FormEvent) {
    event.preventDefault()
    if (!workspaceId || !name.trim()) return
    setSubmitting(true)
    setIssued(null)
    setIssuedOwner(null)
    setError('')
    try {
      const issueForUser = isGlobalAdmin && credentialAudience === 'user'
      if (issueForUser && !targetUserId) return
      const result = issueForUser
        ? await issueAdminMcpCredential({
          user_id: targetUserId,
          name: name.trim(),
          ttl_days: ttlDays,
          workspace_id: workspaceId,
        })
        : await issueMcpCredential({
          name: name.trim(),
          ttl_days: ttlDays,
          workspace_id: workspaceId,
        })
      setIssued(result)
      setIssuedOwner(result.issued_for || (isGlobalAdmin ? {
        user_id: '',
        display_name: bootstrap.session.display_name,
        email: bootstrap.session.email,
        global_role: 'admin',
      } : null))
      if (!issueForUser) setCredentials((current) => [result.credential, ...current])
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('mcp.issueError'))
    } finally {
      setSubmitting(false)
    }
  }

  async function revokeCredential(credentialId: string) {
    setError('')
    try {
      await revokeMcpCredential(credentialId)
      setCredentials((current) => current.map((credential) => (
        credential.id === credentialId
          ? { ...credential, status: 'revoked' }
          : credential
      )))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('mcp.revokeError'))
    }
  }

  async function copyValue(
    kind: 'key' | 'config' | 'json' | 'python',
    value: string,
  ) {
    await navigator.clipboard.writeText(value)
    setCopied(kind)
    window.setTimeout(() => setCopied(''), 1800)
  }

  return (
    <section className={`mcp-page ${embedded ? 'mcp-page--embedded' : ''}`}>
      <div className="mcp-backdrop" aria-hidden="true">
        <span />
        <span />
        <span />
      </div>

      <header className="mcp-hero">
        <div>
          <span className="mcp-eyebrow"><PlugZap />{t('mcp.eyebrow')}</span>
          <h1>{t('mcp.title')}</h1>
          <p>{t('mcp.description')}</p>
        </div>
        <div className="mcp-trust-card">
          <ShieldCheck />
          <div>
            <strong>{t('mcp.serverEnforced')}</strong>
            <span>{t('mcp.serverEnforcedBody')}</span>
          </div>
        </div>
      </header>

      {error && <div className="mcp-alert" role="alert">{error}</div>}

      <div className="mcp-layout">
        <form className="mcp-export-card" onSubmit={(event) => void createCredential(event)}>
          <div className="mcp-card-heading">
            <span><ServerCog /></span>
            <div>
              <small>{t('mcp.exportLabel')}</small>
              <h2>{t('mcp.exportTitle')}</h2>
            </div>
          </div>

          {!embedded && (
            <label className="mcp-field">
              <span>{t('mcp.workspace')}</span>
              <Select
                value={workspaceId}
                ariaLabel={t('mcp.workspace')}
                options={workspaces.map((workspace) => ({
                  value: workspace.workspace_id,
                  label: `${workspace.name} · ${workspace.access_role}`,
                }))}
                onChange={setWorkspaceId}
              />
              <small>{t('mcp.workspaceHint')}</small>
            </label>
          )}

          {isGlobalAdmin && (
            <>
              <div className="mcp-field">
                <span id="mcp-credential-type-label">{t('mcp.credentialType')}</span>
                <div className="mcp-audience-control" role="radiogroup" aria-labelledby="mcp-credential-type-label">
                  <button
                    type="button"
                    role="radio"
                    aria-checked={credentialAudience === 'admin'}
                    className={credentialAudience === 'admin' ? 'is-selected' : ''}
                    onClick={() => setCredentialAudience('admin')}
                  >
                    <strong>{t('mcp.credentialTypeAdmin')}</strong>
                    <small>{bootstrap.session.display_name}</small>
                  </button>
                  <button
                    type="button"
                    role="radio"
                    aria-checked={credentialAudience === 'user'}
                    className={credentialAudience === 'user' ? 'is-selected' : ''}
                    onClick={() => setCredentialAudience('user')}
                  >
                    <strong>{t('mcp.credentialTypeUser')}</strong>
                    <small>{t('mcp.credentialTypeHint')}</small>
                  </button>
                </div>
              </div>
              {credentialAudience === 'user' && (
                <label className="mcp-field">
                  <span>{t('mcp.userAccount')}</span>
                  {eligibleTargetUsers.length ? (
                    <Select
                      value={targetUserId}
                      ariaLabel={t('mcp.userAccount')}
                      options={eligibleTargetUsers.map((user) => ({
                        value: user.id,
                        label: `${user.display_name} · ${user.email}`,
                      }))}
                      onChange={setTargetUserId}
                    />
                  ) : (
                    <div className="mcp-field-empty" role="status">{t('mcp.noUserAccounts')}</div>
                  )}
                  <small>{t('mcp.userAccountHint')}</small>
                </label>
              )}
            </>
          )}

          <label className="mcp-field">
            <span>{t('mcp.agentName')}</span>
            <input
              value={name}
              maxLength={96}
              onChange={(event) => setName(event.target.value)}
              placeholder={t('mcp.agentNamePlaceholder')}
            />
          </label>

          <label className="mcp-field">
            <span>{t('mcp.ttl')}</span>
            <Select
              value={String(ttlDays)}
              ariaLabel={t('mcp.ttl')}
              options={[7, 30, 90, 365].map((days) => ({
                value: String(days),
                label: `${days} ${t('mcp.days')}`,
              }))}
              onChange={(value) => setTtlDays(Number(value))}
            />
          </label>

          <div className="mcp-policy">
            <LockKeyhole />
            <p><strong>{t('mcp.policyTitle')}</strong>{t('mcp.policyBody')}</p>
          </div>

          <Button
            type="submit"
            variant="primary"
            size="lg"
            fullWidth
            disabled={!workspaceId || !name.trim() || submitting || (isGlobalAdmin && credentialAudience === 'user' && !targetUserId)}
            leadingIcon={<KeyRound />}
          >
            {submitting ? t('mcp.issuing') : t('mcp.issue')}
          </Button>
        </form>

        <section className="mcp-credentials-card">
          <div className="mcp-card-heading mcp-card-heading--list">
            <span><KeyRound /></span>
            <div>
              <small>{t('mcp.credentialsLabel')}</small>
              <h2>{t('mcp.credentialsTitle')}</h2>
            </div>
            <Button
              variant="ghost"
              size="icon"
              aria-label={t('mcp.refresh')}
              onClick={() => void loadCredentials()}
              leadingIcon={<RotateCcw />}
            />
          </div>

          {loading && <div className="mcp-empty">{t('mcp.loading')}</div>}
          {!loading && credentials.length === 0 && (
            <div className="mcp-empty">
              <KeyRound />
              <strong>{t('mcp.emptyTitle')}</strong>
              <span>{t('mcp.emptyBody')}</span>
            </div>
          )}
          {!loading && credentials.length > 0 && (
            <div className="mcp-credential-list">
              {credentials.map((credential) => (
                <article key={credential.id} className="mcp-credential">
                  <span className={`mcp-status mcp-status--${credential.status}`} />
                  <div className="mcp-credential-copy">
                    <strong>{credential.name}</strong>
                    <span>
                      {credential.project_ids.join(', ') || t('mcp.legacyScope')}
                    </span>
                    <small><Clock3 />{formatDate(credential.expires_at, locale)}</small>
                  </div>
                  <span className={`mcp-status-label mcp-status-label--${credential.status}`}>
                    {t(`mcp.status.${credential.status}`)}
                  </span>
                  {credential.status === 'active' && (
                    <Button
                      variant="danger"
                      size="icon"
                      aria-label={t('mcp.revoke')}
                      title={t('mcp.revoke')}
                      onClick={() => void revokeCredential(credential.id)}
                      leadingIcon={<Trash2 />}
                    />
                  )}
                </article>
              ))}
            </div>
          )}
        </section>
      </div>

      {issued && (
        <div className="mcp-secret-backdrop" role="dialog" aria-modal="true" aria-labelledby="mcp-secret-title">
          <section className="mcp-secret-dialog">
            <Button
              variant="ghost"
              size="icon"
              className="mcp-secret-close"
              aria-label={t('common.close')}
              onClick={() => { setIssued(null); setIssuedOwner(null) }}
              leadingIcon={<X />}
            />
            <div className="mcp-secret-icon"><Link2 /></div>
            <span>{t('mcp.readyLabel')}</span>
            <h2 id="mcp-secret-title">{t('mcp.readyTitle')}</h2>
            <p>{t('mcp.readyBody')}</p>

            <div className="mcp-secret-meta">
              {isGlobalAdmin && issuedOwner && <div><span>{t('mcp.issuedFor')}</span><strong>{issuedOwner.display_name} · {issuedOwner.email}</strong></div>}
              <div><span>{t('mcp.endpoint')}</span><strong>{issued.connection.url}</strong></div>
              <div><span>{t('mcp.scope')}</span><strong>{issued.connection.workspace_id}</strong></div>
              <div><span>{t('mcp.role')}</span><strong>{issued.connection.workspace_role}</strong></div>
            </div>

            <div className="mcp-secret-value">
              <code>{issued.api_key}</code>
              <Button
                variant="secondary"
                size="sm"
                onClick={() => void copyValue('key', issued.api_key)}
                leadingIcon={copied === 'key' ? <Check /> : <Clipboard />}
              >
                {copied === 'key' ? t('mcp.copied') : t('mcp.copyKey')}
              </Button>
            </div>

            <div className="mcp-backend-export">
              <header>
                <div>
                  <small>{t('mcp.backendExportLabel')}</small>
                  <strong>{t('mcp.backendExportTitle')}</strong>
                </div>
                <code>Python · {issued.connection.sdk.python_package}</code>
              </header>
              <p>{t('mcp.backendExportBody')}</p>
              <pre aria-label={t('mcp.sdkPreview')}><code>{pythonSdkSnippet(issued)}</code></pre>
            </div>

            <div className="mcp-export-actions">
              <Button
                variant="primary"
                onClick={() => void copyValue('python', pythonSdkSnippet(issued))}
                leadingIcon={copied === 'python' ? <Check /> : <Clipboard />}
              >
                {copied === 'python' ? t('mcp.copied') : t('mcp.copyPython')}
              </Button>
              <Button
                variant="secondary"
                onClick={() => void copyValue('json', sdkIntegrationJson(issued))}
                leadingIcon={copied === 'json' ? <Check /> : <Clipboard />}
              >
                {copied === 'json' ? t('mcp.copied') : t('mcp.copySdkJson')}
              </Button>
              <Button
                variant="secondary"
                onClick={() => void copyValue('config', resolvedConfig(issued))}
                leadingIcon={copied === 'config' ? <Check /> : <Clipboard />}
              >
                {copied === 'config' ? t('mcp.copied') : t('mcp.copyConfig')}
              </Button>
            </div>
          </section>
        </div>
      )}
    </section>
  )
}
