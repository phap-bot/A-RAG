import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'

import i18n, { LANGUAGE_STORAGE_KEY } from './i18n'
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
import { AuthPage } from './pages/AuthPage'
import { HomePage } from './pages/HomePage'
import { ProjectPage } from './pages/ProjectPage'
import { WorkspacePage } from './pages/WorkspacePage'
import type { AuthUser, DocumentRow, UiBootstrap, ViewName, WorkspaceRecord, WorkspaceSection } from './types'

function getPreferredLanguage(fallback = 'vi'): string {
  return window.localStorage.getItem(LANGUAGE_STORAGE_KEY)?.split('-')[0] || fallback
}

async function applyLanguage(locale: string) {
  const nextLocale = locale.split('-')[0]
  window.localStorage.setItem(LANGUAGE_STORAGE_KEY, nextLocale)
  await i18n.changeLanguage(nextLocale)
  document.documentElement.lang = nextLocale
  document.documentElement.dataset.locale = nextLocale
}

const NAVIGATION_STORAGE_KEY = 'ai-document.navigation'
const AUTH_VIEWS: ViewName[] = ['signin', 'signup', 'forgot', 'reset']

type NavigationHistoryMode = 'push' | 'replace' | 'none'

type NavigationSnapshot = {
  view: ViewName
  workspaceId: string | null
  analysisDocumentId: string | null
  projectSection: WorkspaceSection
}

function isObjectLike(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object'
}

function isViewName(value: unknown): value is ViewName {
  return typeof value === 'string' && ['home', 'signin', 'signup', 'forgot', 'reset', 'dashboard', 'workspaces', 'connections', 'admin', 'project', 'assistant'].includes(value)
}

function isWorkspaceSection(value: unknown): value is WorkspaceSection {
  return typeof value === 'string' && ['overview', 'documents', 'members', 'graph', 'settings', 'evaluator', 'mcp'].includes(value)
}

function createNavigationSnapshot({
  view,
  workspaceId,
  analysisDocumentId,
  projectSection,
}: NavigationSnapshot): NavigationSnapshot {
  return {
    view,
    workspaceId,
    analysisDocumentId,
    projectSection,
  }
}

function parseNavigationSnapshot(value: unknown): NavigationSnapshot | null {
  if (!isObjectLike(value)) return null

  const view = value.view
  const workspaceId = value.workspaceId
  const analysisDocumentId = value.analysisDocumentId
  const projectSection = value.projectSection

  if (!isViewName(view) || !isWorkspaceSection(projectSection)) return null
  if (workspaceId !== null && typeof workspaceId !== 'string') return null
  if (analysisDocumentId !== null && typeof analysisDocumentId !== 'string') return null

  return createNavigationSnapshot({ view, workspaceId, analysisDocumentId, projectSection })
}

function readNavigationSnapshot(): NavigationSnapshot | null {
  try {
    const raw = window.localStorage.getItem(NAVIGATION_STORAGE_KEY)
      || window.sessionStorage.getItem(NAVIGATION_STORAGE_KEY)
    return raw ? parseNavigationSnapshot(JSON.parse(raw)) : null
  } catch {
    return null
  }
}

function writeNavigationSnapshot(snapshot: NavigationSnapshot) {
  const serialized = JSON.stringify(snapshot)
  window.localStorage.setItem(NAVIGATION_STORAGE_KEY, serialized)
  window.sessionStorage.setItem(NAVIGATION_STORAGE_KEY, serialized)
}

function clearNavigationSnapshot() {
  window.localStorage.removeItem(NAVIGATION_STORAGE_KEY)
  window.sessionStorage.removeItem(NAVIGATION_STORAGE_KEY)
}

function writeHistorySnapshot(snapshot: NavigationSnapshot, mode: Exclude<NavigationHistoryMode, 'none'> = 'replace') {
  const state = { navigationSnapshot: snapshot }
  if (mode === 'push') {
    window.history.pushState(state, document.title, window.location.pathname)
    return
  }
  window.history.replaceState(state, document.title, window.location.pathname)
}

function readHistorySnapshot(value: unknown): NavigationSnapshot | null {
  if (!isObjectLike(value)) return null
  return parseNavigationSnapshot(value.navigationSnapshot)
}

function seedHistoryStack(snapshot: NavigationSnapshot) {
  if (!snapshot.workspaceId || AUTH_VIEWS.includes(snapshot.view) || snapshot.view === 'home' || snapshot.view === 'dashboard' || snapshot.view === 'workspaces' || snapshot.view === 'admin') {
    writeHistorySnapshot(snapshot, 'replace')
    return
  }

  const workspaceSnapshot = createNavigationSnapshot({
    view: 'workspaces',
    workspaceId: snapshot.workspaceId,
    analysisDocumentId: null,
    projectSection: snapshot.projectSection,
  })
  const projectSnapshot = createNavigationSnapshot({
    view: 'project',
    workspaceId: snapshot.workspaceId,
    analysisDocumentId: null,
    projectSection: snapshot.projectSection,
  })

  writeHistorySnapshot(workspaceSnapshot, 'replace')
  writeHistorySnapshot(projectSnapshot, 'push')

  if (snapshot.view === 'assistant') {
    writeHistorySnapshot(snapshot, 'push')
    return
  }

  writeHistorySnapshot(projectSnapshot, 'replace')
}

function App() {
  const { t } = useTranslation()
  const [resetToken] = useState(() => new URLSearchParams(window.location.search).get('reset_token')?.trim() || '')
  const [view, setView] = useState<ViewName>(() => resetToken ? 'reset' : 'home')
  const [authUser, setAuthUser] = useState<AuthUser | null>(null)
  const [bootstrap, setBootstrap] = useState<UiBootstrap | null>(null)
  const [workspaces, setWorkspaces] = useState<WorkspaceRecord[]>([])
  const [workspace, setWorkspace] = useState<WorkspaceRecord | null>(null)
  const [documents, setDocuments] = useState<DocumentRow[]>([])
  const [analysisDocument, setAnalysisDocument] = useState<DocumentRow | null>(null)
  const [projectSection, setProjectSection] = useState<WorkspaceSection>('documents')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  function pushNavigationSnapshot(snapshot: NavigationSnapshot, mode: NavigationHistoryMode = 'push') {
    if (mode === 'none') return
    writeHistorySnapshot(snapshot, mode)
  }

  async function loadApplication() {
    setLoading(true)
    setError(null)
    const storedNavigation = resetToken ? null : readNavigationSnapshot()
    try {
      const [shell, session] = await Promise.all([getUiBootstrap(), getAuthSession()])
      const locale = getPreferredLanguage(shell.locale)
      await applyLanguage(locale)
      setBootstrap({
        ...shell,
        locale,
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
        const restoredWorkspace = storedNavigation?.workspaceId
          ? records.find((item) => item.workspace_id === storedNavigation.workspaceId) || records[0] || null
          : records[0] || null
        const restoredSection = storedNavigation?.projectSection || 'documents'
        let restoredView: ViewName = storedNavigation?.view || 'home'
        let restoredDocuments: DocumentRow[] = []
        let restoredAnalysisDocument: DocumentRow | null = null

        if (restoredView === 'admin' && session.user?.role !== 'admin') {
          restoredView = 'workspaces'
        }
        if ((restoredView === 'project' || restoredView === 'assistant') && restoredWorkspace) {
          restoredDocuments = await getDocuments(restoredWorkspace.workspace_id)
          restoredAnalysisDocument = storedNavigation?.analysisDocumentId
            ? restoredDocuments.find((item) => item.id === storedNavigation.analysisDocumentId) || null
            : null
          if (restoredView === 'assistant' && !restoredAnalysisDocument) {
            restoredAnalysisDocument = restoredDocuments[0] || null
          }
        } else if (restoredView === 'project' || restoredView === 'assistant') {
          restoredView = 'workspaces'
        } else if (AUTH_VIEWS.includes(restoredView)) {
          restoredView = 'home'
        }

        setWorkspaces(records)
        setWorkspace(restoredWorkspace)
        setDocuments(restoredDocuments)
        setAnalysisDocument(restoredAnalysisDocument)
        setProjectSection(restoredSection)
        setView(restoredView)

        const initialSnapshot = createNavigationSnapshot({
          view: restoredView,
          workspaceId: restoredWorkspace?.workspace_id || null,
          analysisDocumentId: restoredAnalysisDocument?.id || null,
          projectSection: restoredSection,
        })
        writeNavigationSnapshot(initialSnapshot)
        seedHistoryStack(initialSnapshot)
      } else {
        setWorkspaces([])
        setWorkspace(null)
        setDocuments([])
        setAnalysisDocument(null)
        setProjectSection('documents')
        const restoredView = resetToken
          ? 'reset'
          : (storedNavigation && (storedNavigation.view === 'home' || AUTH_VIEWS.includes(storedNavigation.view)) ? storedNavigation.view : 'home')
        setView(restoredView)

        const initialSnapshot = createNavigationSnapshot({
          view: restoredView,
          workspaceId: null,
          analysisDocumentId: null,
          projectSection: 'documents',
        })
        writeNavigationSnapshot(initialSnapshot)
        writeHistorySnapshot(initialSnapshot, 'replace')
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('state.errorTitle'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (resetToken) {
      window.history.replaceState({}, document.title, window.location.pathname)
    }
    void loadApplication()
  }, [resetToken])

  useEffect(() => {
    const resolvedLanguage = (i18n.resolvedLanguage || bootstrap?.locale || getPreferredLanguage()).split('-')[0]
    document.documentElement.lang = resolvedLanguage
    document.documentElement.dataset.locale = resolvedLanguage
  }, [bootstrap?.locale, i18n.resolvedLanguage])

  useEffect(() => {
    if (loading || error) return
    const snapshot = createNavigationSnapshot({
      view,
      workspaceId: workspace?.workspace_id || null,
      analysisDocumentId: analysisDocument?.id || null,
      projectSection,
    })
    writeNavigationSnapshot(snapshot)
    writeHistorySnapshot(snapshot, 'replace')
  }, [analysisDocument?.id, error, loading, projectSection, view, workspace?.workspace_id])

  function showDashboard(history: NavigationHistoryMode = 'push') {
    if (!authUser) {
      navigate('signin')
      return
    }
    setError(null)
    setDocuments([])
    setAnalysisDocument(null)
    setView('dashboard')
    pushNavigationSnapshot(createNavigationSnapshot({
      view: 'dashboard',
      workspaceId: workspace?.workspace_id || null,
      analysisDocumentId: null,
      projectSection,
    }), history)
  }
  async function showWorkspaces(options: { history?: NavigationHistoryMode; workspaceId?: string | null } = {}) {
    if (!authUser) {
      setView('signin')
      pushNavigationSnapshot(createNavigationSnapshot({
        view: 'signin',
        workspaceId: null,
        analysisDocumentId: null,
        projectSection,
      }), options.history || 'push')
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
      pushNavigationSnapshot(createNavigationSnapshot({
        view: 'workspaces',
        workspaceId: selectedWorkspace?.workspace_id || null,
        analysisDocumentId: null,
        projectSection,
      }), options.history || 'push')
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
    const snapshot = createNavigationSnapshot({
      view: 'workspaces',
      workspaceId: selectedWorkspace?.workspace_id || null,
      analysisDocumentId: null,
      projectSection,
    })
    writeNavigationSnapshot(snapshot)
    writeHistorySnapshot(snapshot, 'replace')
  }

  async function changeLanguage(locale: string) {
    await applyLanguage(locale)
    setBootstrap((current) => (current ? { ...current, locale } : current))
    try {
      await setLanguagePreference(locale)
    } catch {
      // UI language is intentionally local-first; backend preference sync is non-blocking.
    }
  }

  async function openProject(
    selectedWorkspace: WorkspaceRecord,
    options: { history?: NavigationHistoryMode; section?: WorkspaceSection } = {},
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
      pushNavigationSnapshot(createNavigationSnapshot({
        view: 'project',
        workspaceId: selectedWorkspace.workspace_id,
        analysisDocumentId: null,
        projectSection: nextSection,
      }), options.history || 'push')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('state.errorTitle'))
    }
  }

  async function openAnalysis(
    document?: DocumentRow,
    options: {
      history?: NavigationHistoryMode
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
      pushNavigationSnapshot(createNavigationSnapshot({
        view: 'assistant',
        workspaceId: targetWorkspace.workspace_id,
        analysisDocumentId: selectedDocument?.id || null,
        projectSection: nextSection,
      }), options.history || 'push')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('state.errorTitle'))
    }
  }

  async function completeAuthentication(user: AuthUser) {
    setLoading(true)
    setError(null)
    try {
      const [shell, records] = await Promise.all([getUiBootstrap(), getWorkspaces()])
      const locale = getPreferredLanguage(shell.locale)
      await applyLanguage(locale)
      setBootstrap({
        ...shell,
        locale,
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
      writeHistorySnapshot(createNavigationSnapshot({
        view: 'dashboard',
        workspaceId: records[0]?.workspace_id || null,
        analysisDocumentId: null,
        projectSection: 'documents',
      }), 'replace')
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
      const locale = getPreferredLanguage(shell.locale)
      await applyLanguage(locale)
      setBootstrap({ ...shell, locale })
      setAuthUser(null)
      setWorkspaces([])
      setWorkspace(null)
      setDocuments([])
      setAnalysisDocument(null)
      setProjectSection('documents')
      setView('home')
      clearNavigationSnapshot()
      writeHistorySnapshot(createNavigationSnapshot({
        view: 'home',
        workspaceId: null,
        analysisDocumentId: null,
        projectSection: 'documents',
      }), 'replace')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('state.errorTitle'))
    }
  }

  function navigate(nextView: ViewName) {
    if (nextView === 'home') {
      setAnalysisDocument(null)
      setView('home')
      pushNavigationSnapshot(createNavigationSnapshot({
        view: 'home',
        workspaceId: workspace?.workspace_id || null,
        analysisDocumentId: null,
        projectSection,
      }))
      return
    }

    if (AUTH_VIEWS.includes(nextView)) {
      setView(nextView)
      pushNavigationSnapshot(createNavigationSnapshot({
        view: nextView,
        workspaceId: null,
        analysisDocumentId: null,
        projectSection: 'documents',
      }))
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
      pushNavigationSnapshot(createNavigationSnapshot({
        view: 'connections',
        workspaceId: workspace?.workspace_id || null,
        analysisDocumentId: null,
        projectSection,
      }))
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
      pushNavigationSnapshot(createNavigationSnapshot({
        view: 'admin',
        workspaceId: workspace?.workspace_id || null,
        analysisDocumentId: null,
        projectSection,
      }))
    }
    if (nextView === 'project' && workspace) void openProject(workspace)
    if (nextView === 'assistant') void openAnalysis()
  }

  useEffect(() => {
    function onPopState(event: PopStateEvent) {
      const snapshot = readHistorySnapshot(event.state) || readNavigationSnapshot()
      if (!snapshot) return

      if (!authUser) {
        setDocuments([])
        setAnalysisDocument(null)
        setProjectSection(snapshot.projectSection)
        setView(snapshot.view === 'home' || AUTH_VIEWS.includes(snapshot.view) ? snapshot.view : 'home')
        return
      }

      const restoredWorkspace = snapshot.workspaceId
        ? workspaces.find((item) => item.workspace_id === snapshot.workspaceId) || workspace || null
        : workspace || workspaces[0] || null

      if (snapshot.view === 'dashboard') {
        showDashboard('none')
        return
      }
      if (snapshot.view === 'admin') {
        setView(authUser.role === 'admin' ? 'admin' : 'workspaces')
        return
      }
      if (snapshot.view === 'connections') {
        setView('connections')
        return
      }

      if (snapshot.view === 'workspaces') {
        void showWorkspaces({ history: 'none', workspaceId: snapshot.workspaceId })
        return
      }

      if (snapshot.view === 'project' && restoredWorkspace) {
        void openProject(restoredWorkspace, { history: 'none', section: snapshot.projectSection })
        return
      }

      if (snapshot.view === 'assistant' && restoredWorkspace) {
        void openAnalysis(undefined, {
          history: 'none',
          workspaceOverride: restoredWorkspace,
          projectSection: snapshot.projectSection,
          documentId: snapshot.analysisDocumentId,
        })
        return
      }

      setDocuments([])
      setAnalysisDocument(null)
      setProjectSection(snapshot.projectSection)
      setView(snapshot.view === 'home' ? 'home' : 'workspaces')
    }

    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [authUser, projectSection, workspace, workspaces])

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

      {!loading && !error && bootstrap && workspace && view === 'project' && <ProjectPage bootstrap={bootstrap} workspace={workspace} workspaces={workspaces} documents={documents} initialSection={projectSection} onSectionChange={setProjectSection} onDocumentsChange={setDocuments} onBack={() => void showWorkspaces({ history: 'replace' })} onAnalyze={(document) => void openAnalysis(document)} />}

      {!loading && !error && bootstrap && workspace && view === 'assistant' && <AssistantPage bootstrap={bootstrap} workspace={workspace} documents={documents} initialDocument={analysisDocument} onDocumentChange={setAnalysisDocument} onClose={() => void openProject(workspace, { history: 'replace', section: projectSection })} onBack={() => void showWorkspaces({ history: 'replace' })} />}
    </main>
  )
}

export default App
