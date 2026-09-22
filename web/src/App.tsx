import { useCallback, useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import i18n from './i18n'
import {
  createWorkspace,
  deleteWorkspace,
  getAuthSession,
  getDocuments,
  getUiBootstrap,
  getWorkspaces,
  setLanguagePreference,
  signOut,
} from './api'
import { BrandHeader } from './components/BrandHeader'
import { Button } from './components/ui/Button'
import { DashboardPage } from './pages/DashboardPage'
import { UserAccessPage } from './pages/UserAccessPage'
import { AgentConnectionsPage } from './pages/AgentConnectionsPage'
import { AssistantPage } from './pages/AssistantPage'
import { AgentFlowPage } from './pages/AgentFlowPage'
import { AuthPage } from './pages/AuthPage'
import { HomePage } from './pages/HomePage'
import { ProjectPage } from './pages/ProjectPage'
import { WorkspacePage } from './pages/WorkspacePage'
import type { AuthUser, DocumentRow, UiBootstrap, ViewName, WorkspaceRecord, WorkspaceSection } from './types'

async function applyLanguage(locale: string) {
  const nextLocale = locale.split('-')[0]
  await i18n.changeLanguage(nextLocale)
  document.documentElement.lang = nextLocale
  document.documentElement.dataset.locale = nextLocale
}

const AUTH_VIEWS: ViewName[] = ['signin', 'signup', 'forgot', 'reset']
const APP_BASE_PATH = (import.meta.env.BASE_URL || '/').replace(/\/+$/, '')

type AppRoute = {
  view: ViewName
  workspaceId: string | null
  documentId: string | null
  projectSection: WorkspaceSection
}

const DEFAULT_ROUTE: AppRoute = {
  view: 'home',
  workspaceId: null,
  documentId: null,
  projectSection: 'documents',
}

function isWorkspaceSection(value: string | null): value is WorkspaceSection {
  return value === 'overview'
    || value === 'documents'
    || value === 'members'
    || value === 'graph'
    || value === 'settings'
    || value === 'evaluator'
    || value === 'mcp'
}

function readAppRoute(): AppRoute {
  const pathname = APP_BASE_PATH && window.location.pathname.startsWith(`${APP_BASE_PATH}/`)
    ? window.location.pathname.slice(APP_BASE_PATH.length)
    : APP_BASE_PATH && window.location.pathname === APP_BASE_PATH
      ? '/'
      : window.location.pathname
  const path = pathname.replace(/^\/+|\/+$/g, '')
  const segments = path ? path.split('/') : []
  const query = new URLSearchParams(window.location.search)

  if (segments[0] === 'auth' && AUTH_VIEWS.includes(segments[1] as ViewName)) {
    return { ...DEFAULT_ROUTE, view: segments[1] as ViewName }
  }
  if (segments[0] === 'dashboard') return { ...DEFAULT_ROUTE, view: 'dashboard' }
  if (segments[0] === 'connections') return { ...DEFAULT_ROUTE, view: 'connections' }
  if (segments[0] === 'admin') return { ...DEFAULT_ROUTE, view: 'admin' }
  if (segments[0] === 'agent-flow') return { ...DEFAULT_ROUTE, view: 'agent-flow', workspaceId: query.get('workspace') }
  if (segments[0] !== 'workspaces') return DEFAULT_ROUTE
  if (segments.length === 1) return { ...DEFAULT_ROUTE, view: 'workspaces' }

  const workspaceId = decodeURIComponent(segments[1])
  const sectionParam = query.get('section')
  const projectSection: WorkspaceSection = isWorkspaceSection(sectionParam) ? sectionParam : 'documents'
  if (segments[2] === 'assistant') {
    return {
      view: 'assistant',
      workspaceId,
      documentId: query.get('document'),
      projectSection,
    }
  }
  if (segments[2] === 'agent-flow') {
    return { view: 'agent-flow', workspaceId, documentId: null, projectSection }
  }
  if (segments[2] === 'project') {
    return { view: 'project', workspaceId, documentId: null, projectSection }
  }
  return { ...DEFAULT_ROUTE, view: 'workspaces' }
}

function appRoutePath(route: AppRoute): string {
  const params = new URLSearchParams()
  let path = '/'
  if (route.view === 'project' || route.view === 'assistant' || route.view === 'agent-flow') {
    if (route.projectSection !== 'documents') params.set('section', route.projectSection)
    if (route.view === 'assistant' && route.documentId) params.set('document', route.documentId)
    const query = params.toString()
    path = `/workspaces/${encodeURIComponent(route.workspaceId || '')}/${route.view}${query ? `?${query}` : ''}`
  } else if (route.view === 'workspaces') {
    path = '/workspaces'
  } else if (route.view === 'dashboard') {
    path = '/dashboard'
  } else if (route.view === 'connections') {
    path = '/connections'
  } else if (route.view === 'admin') {
    path = '/admin'
  } else if (AUTH_VIEWS.includes(route.view)) {
    path = `/auth/${route.view}`
  }
  return `${APP_BASE_PATH}${path}` || '/'
}

function syncAppRoute(route: AppRoute, mode: 'push' | 'replace' = 'push') {
  const path = appRoutePath(route)
  if (mode === 'push') {
    window.history.pushState(null, document.title, path)
  } else {
    window.history.replaceState(null, document.title, path)
  }
}

function routeForState(
  view: ViewName,
  workspaceId: string | null,
  projectSection: WorkspaceSection,
  documentId: string | null = null,
): AppRoute {
  return { view, workspaceId, projectSection, documentId }
}

function App() {
  const { t } = useTranslation()
  const [resetToken] = useState(() => new URLSearchParams(window.location.search).get('reset_token')?.trim() || '')
  const [view, setView] = useState<ViewName>(() => readAppRoute().view === 'home' && resetToken ? 'reset' : readAppRoute().view)
  const [authUser, setAuthUser] = useState<AuthUser | null>(null)
  const [bootstrap, setBootstrap] = useState<UiBootstrap | null>(null)
  const [workspaces, setWorkspaces] = useState<WorkspaceRecord[]>([])
  const [workspace, setWorkspace] = useState<WorkspaceRecord | null>(null)
  const [documents, setDocuments] = useState<DocumentRow[]>([])
  const [analysisDocument, setAnalysisDocument] = useState<DocumentRow | null>(null)
  const [projectSection, setProjectSection] = useState<WorkspaceSection>('documents')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  async function loadApplication() {
    setLoading(true)
    setError(null)
    const requestedRoute = readAppRoute()
    try {
      const [shell, session] = await Promise.all([getUiBootstrap(), getAuthSession()])
      await applyLanguage(shell.locale)
      setBootstrap({
        ...shell,
        locale: shell.locale,
        session: session.user ? {
          ...shell.session,
          authenticated: true,
          display_name: session.user.display_name,
          email: session.user.email,
          role: session.user.role,
          mode: 'database',
        } : shell.session,
      })
      setAuthUser(session.user)
      if (session.authenticated) {
        const records = await getWorkspaces()
        const requestedWorkspace = requestedRoute.workspaceId
          ? records.find((item) => item.workspace_id === requestedRoute.workspaceId) || null
          : null
        let nextView: ViewName = requestedRoute.view
        let nextWorkspace = requestedWorkspace || records[0] || null
        let nextDocuments: DocumentRow[] = []
        let nextDocument: DocumentRow | null = null

        if (AUTH_VIEWS.includes(nextView) || nextView === 'home') nextView = 'home'
        if (nextView === 'admin' && session.user?.role !== 'admin') nextView = 'workspaces'
        if ((nextView === 'project' || nextView === 'assistant' || nextView === 'agent-flow') && !requestedWorkspace) nextView = 'workspaces'
        if (nextView === 'project' || nextView === 'assistant' || nextView === 'agent-flow') {
          nextDocuments = await getDocuments(nextWorkspace!.workspace_id)
          nextDocument = requestedRoute.documentId
            ? nextDocuments.find((item) => item.id === requestedRoute.documentId) || null
            : nextDocuments[0] || null
          if (nextView === 'assistant' && !nextDocument) nextView = 'project'
        }

        setWorkspaces(records)
        setWorkspace(nextWorkspace)
        setDocuments(nextDocuments)
        setAnalysisDocument(nextDocument)
        setProjectSection(requestedRoute.projectSection)
        setView(nextView)
        syncAppRoute(routeForState(
          nextView,
          nextWorkspace?.workspace_id || null,
          requestedRoute.projectSection,
          nextDocument?.id || null,
        ), 'replace')
      } else {
        setWorkspaces([])
        setWorkspace(null)
        setDocuments([])
        setAnalysisDocument(null)
        setProjectSection('documents')
        const nextView = resetToken ? 'reset' : (AUTH_VIEWS.includes(requestedRoute.view) ? requestedRoute.view : 'home')
        setView(nextView)
        syncAppRoute(routeForState(nextView, null, 'documents'), 'replace')
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('state.errorTitle'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (resetToken) {
      syncAppRoute(routeForState('reset', null, 'documents'), 'replace')
    }
    void loadApplication()
  }, [resetToken])

  useEffect(() => {
    const onPopState = () => void loadApplication()
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  useEffect(() => {
    const resolvedLanguage = (i18n.resolvedLanguage || bootstrap?.locale || 'vi').split('-')[0]
    document.documentElement.lang = resolvedLanguage
    document.documentElement.dataset.locale = resolvedLanguage
  }, [bootstrap?.locale, i18n.resolvedLanguage])

  function showDashboard() {
    if (!authUser) {
      navigate('signin')
      return
    }
    setError(null)
    setDocuments([])
    setAnalysisDocument(null)
    setView('dashboard')
    syncAppRoute(routeForState('dashboard', workspace?.workspace_id || null, projectSection))
  }
  async function showWorkspaces(options: { workspaceId?: string | null } = {}) {
    if (!authUser) {
      setView('signin')
      syncAppRoute(routeForState('signin', null, 'documents'))
      return
    }
    setError(null)
    try {
      const records = await getWorkspaces()
      const selectedWorkspace = options.workspaceId
        ? records.find((item) => item.workspace_id === options.workspaceId) || records[0] || null
        : (workspace && records.some((item) => item.workspace_id === workspace.workspace_id) ? workspace : records[0] || null)
      setWorkspaces(records)
      setWorkspace(selectedWorkspace)
      setDocuments([])
      setAnalysisDocument(null)
      setView('workspaces')
      syncAppRoute(routeForState('workspaces', selectedWorkspace?.workspace_id || null, 'documents'))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('state.errorTitle'))
    }
  }

  async function searchWorkspaces(query: string) {
    setWorkspaces(await getWorkspaces(query))
  }

  async function addWorkspace(name: string) {
    const created = await createWorkspace(name)
    setWorkspaces((current) => [...current, created])
    setWorkspace(created)
    return created
  }

  async function removeWorkspace(
    target: WorkspaceRecord,
    confirmation: string,
  ) {
    await deleteWorkspace(target.workspace_id, confirmation)
    const remaining = workspaces.filter(
      (item) => item.workspace_id !== target.workspace_id,
    )
    const selectedWorkspace = workspace?.workspace_id === target.workspace_id
      ? remaining[0] || null
      : workspace && remaining.some(
        (item) => item.workspace_id === workspace.workspace_id,
      )
        ? workspace
        : remaining[0] || null

    setWorkspaces(remaining)
    setWorkspace(selectedWorkspace)
    setDocuments([])
    setAnalysisDocument(null)
    syncAppRoute(routeForState('workspaces', selectedWorkspace?.workspace_id || null, 'documents'), 'replace')
  }

  async function changeLanguage(locale: string) {
    await applyLanguage(locale)
    setBootstrap((current) => (current ? { ...current, locale } : current))
    try {
      await setLanguagePreference(locale)
    } catch {
      // Backend preference sync is non-blocking; no browser storage is used.
    }
  }

  const changeProjectSection = useCallback((nextSection: WorkspaceSection) => {
    setProjectSection(nextSection)
    if (view === 'project' && workspace) {
      syncAppRoute(routeForState('project', workspace.workspace_id, nextSection, null), 'replace')
    }
  }, [view, workspace])

  const changeAnalysisDocument = useCallback((nextDocument: DocumentRow | null) => {
    setAnalysisDocument(nextDocument)
    if (view === 'assistant' && workspace) {
      syncAppRoute(routeForState('assistant', workspace.workspace_id, projectSection, nextDocument?.id || null), 'replace')
    }
  }, [projectSection, view, workspace])

  async function openProject(
    selectedWorkspace: WorkspaceRecord,
    options: { section?: WorkspaceSection } = {},
  ) {
    setError(null)
    const nextSection = options.section || projectSection
    setWorkspace(selectedWorkspace)
    setProjectSection(nextSection)
    setView('project')
    try {
      const nextDocuments = await getDocuments(selectedWorkspace.workspace_id)
      setDocuments(nextDocuments)
      setAnalysisDocument(null)
      syncAppRoute(routeForState('project', selectedWorkspace.workspace_id, nextSection, null))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('state.errorTitle'))
    }
  }

  async function openAnalysis(
    document?: DocumentRow,
    options: {
      workspaceOverride?: WorkspaceRecord
      projectSection?: WorkspaceSection
      documentId?: string | null
    } = {},
  ) {
    const targetWorkspace = options.workspaceOverride || workspace
    if (!targetWorkspace) return
    const nextSection = options.projectSection || projectSection
    setError(null)
    try {
      const hasLoadedDocuments = workspace?.workspace_id === targetWorkspace.workspace_id && documents.length > 0
      const availableDocuments = hasLoadedDocuments ? documents : await getDocuments(targetWorkspace.workspace_id)
      const selectedDocument = document
        || (options.documentId ? availableDocuments.find((item) => item.id === options.documentId) || null : null)
        || availableDocuments[0]
        || null
      setWorkspace(targetWorkspace)
      setDocuments(availableDocuments)
      setAnalysisDocument(selectedDocument)
      setProjectSection(nextSection)
      setView('assistant')
      syncAppRoute(routeForState('assistant', targetWorkspace.workspace_id, nextSection, selectedDocument?.id || null))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('state.errorTitle'))
    }
  }

  async function openAgentFlow(selectedWorkspace?: WorkspaceRecord) {
    const targetWorkspace = selectedWorkspace || workspace
    if (!targetWorkspace) return
    setError(null)
    try {
      const availableDocuments = targetWorkspace.workspace_id === workspace?.workspace_id && documents.length > 0
        ? documents
        : await getDocuments(targetWorkspace.workspace_id)
      setWorkspace(targetWorkspace)
      setWorkspaces((current) => current.some((item) => item.workspace_id === targetWorkspace.workspace_id)
        ? current
        : [...current, targetWorkspace])
      setDocuments(availableDocuments)
      setAnalysisDocument(null)
      setProjectSection('documents')
      setView('agent-flow')
      syncAppRoute(routeForState('agent-flow', targetWorkspace.workspace_id, 'documents'))
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('state.errorTitle'))
    }
  }

  async function completeAuthentication(user: AuthUser) {
    setLoading(true)
    setError(null)
    try {
      const [shell, records] = await Promise.all([getUiBootstrap(), getWorkspaces()])
      await applyLanguage(shell.locale)
      setBootstrap({
        ...shell,
        locale: shell.locale,
        session: {
          ...shell.session,
          authenticated: true,
          display_name: user.display_name,
          email: user.email,
          role: user.role,
          mode: 'database',
        },
      })
      setAuthUser(user)
      setWorkspaces(records)
      setWorkspace(records[0] || null)
      setDocuments([])
      setAnalysisDocument(null)
      setProjectSection('documents')
      setView('dashboard')
      syncAppRoute(routeForState('dashboard', records[0]?.workspace_id || null, 'documents'), 'replace')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('state.errorTitle'))
    } finally {
      setLoading(false)
    }
  }

  async function handleSignOut() {
    setError(null)
    try {
      await signOut()
      const shell = await getUiBootstrap()
      await applyLanguage(shell.locale)
      setBootstrap({ ...shell, locale: shell.locale })
      setAuthUser(null)
      setWorkspaces([])
      setWorkspace(null)
      setDocuments([])
      setAnalysisDocument(null)
      setProjectSection('documents')
      setView('home')
      syncAppRoute(routeForState('home', null, 'documents'), 'replace')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('state.errorTitle'))
    }
  }

  function navigate(nextView: ViewName) {
    if (nextView === 'home') {
      setAnalysisDocument(null)
      setView('home')
      syncAppRoute(routeForState('home', null, 'documents'))
      return
    }

    if (AUTH_VIEWS.includes(nextView)) {
      setView(nextView)
      syncAppRoute(routeForState(nextView, null, 'documents'))
      return
    }

    if (nextView === 'dashboard') showDashboard()
    if (nextView === 'workspaces') void showWorkspaces()
    if (nextView === 'connections') {
      if (!authUser) {
        navigate('signin')
        return
      }
      setView('connections')
      syncAppRoute(routeForState('connections', workspace?.workspace_id || null, projectSection))
    }
    if (nextView === 'admin') {
      if (!authUser) {
        navigate('signin')
        return
      }
      if (authUser.role !== 'admin') {
        void showWorkspaces()
        return
      }
      setView('admin')
      syncAppRoute(routeForState('admin', workspace?.workspace_id || null, projectSection))
    }
    if (nextView === 'project' && workspace) void openProject(workspace)
    if (nextView === 'assistant') void openAnalysis()
    if (nextView === 'agent-flow' && workspace) void openAgentFlow(workspace)
  }

  return (
    <main className={`app-shell view-${view}`}>
      {bootstrap && !AUTH_VIEWS.includes(view) && (
        <BrandHeader
          bootstrap={bootstrap}
          view={view}
          hasWorkspace={Boolean(workspace)}
          authenticated={Boolean(authUser)}
          globalRole={authUser?.role || bootstrap.session.role}
          onNavigate={navigate}
          onLanguageChange={(locale) => void changeLanguage(locale)}
          onSignOut={() => void handleSignOut()}
        />
      )}

      {!loading && !error && (view === 'signin' || view === 'signup' || view === 'forgot' || view === 'reset') && (
        <AuthPage mode={view} resetToken={resetToken} onModeChange={(mode) => navigate(mode)} onSuccess={(user) => void completeAuthentication(user)} onBack={() => navigate('home')} />
      )}

      {loading && <section className="system-state" aria-live="polite"><span className="system-loader" /><strong>{t('state.loadingTitle')}</strong><span>{t('state.loadingBody')}</span></section>}

      {!loading && error && <section className="system-state system-error" role="alert"><strong>{t('state.errorTitle')}</strong><span>{error}</span><Button variant="primary" shape="pill" onClick={() => void loadApplication()}>{t('state.retry')}</Button></section>}

      {!loading && !error && bootstrap && view === 'home' && <HomePage bootstrap={bootstrap} workspaces={workspaces} onContinue={() => showDashboard()} />}

      {!loading && !error && bootstrap && authUser && view === 'dashboard' && <DashboardPage bootstrap={bootstrap} workspaces={workspaces} isAdmin={authUser.role === 'admin'} />}

      {!loading && !error && bootstrap && view === 'admin' && authUser?.role === 'admin' && <UserAccessPage bootstrap={bootstrap} />}
      {!loading && !error && bootstrap && view === 'connections' && <AgentConnectionsPage bootstrap={bootstrap} workspaces={workspaces} />}

      {!loading && !error && bootstrap && view === 'workspaces' && <WorkspacePage bootstrap={bootstrap} workspaces={workspaces} onSearch={searchWorkspaces} onCreate={addWorkspace} onDelete={removeWorkspace} onSelect={(selectedWorkspace) => void openProject(selectedWorkspace)} onOpenAssistant={() => void openAnalysis()} />}

      {!loading && !error && bootstrap && workspace && view === 'project' && <ProjectPage bootstrap={bootstrap} workspace={workspace} workspaces={workspaces} documents={documents} initialSection={projectSection} onSectionChange={changeProjectSection} onDocumentsChange={setDocuments} onBack={() => void showWorkspaces()} onAnalyze={(document) => void openAnalysis(document)} />}

      {!loading && !error && bootstrap && workspace && view === 'assistant' && <AssistantPage bootstrap={bootstrap} workspace={workspace} documents={documents} initialDocument={analysisDocument} onDocumentChange={changeAnalysisDocument} onClose={() => void openProject(workspace, { section: projectSection })} onBack={() => void showWorkspaces()} />}

      {!loading && !error && bootstrap && workspace && view === 'agent-flow' && <AgentFlowPage bootstrap={bootstrap} workspace={workspace} documents={documents} onBack={() => void showWorkspaces()} />}
    </main>
  )
}

export default App
