import { Bell, Check, ChevronDown, GitBranch, Globe2, LogOut, Menu, UserRoundCog } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'

import type { UiBootstrap, ViewName } from '../types'
import { Button } from './ui/Button'

type BrandHeaderProps = {
  bootstrap: UiBootstrap
  view: ViewName
  hasWorkspace: boolean
  authenticated: boolean
  globalRole: string
  onNavigate: (view: ViewName) => void
  onLanguageChange: (locale: string) => void
  onSignOut: () => void
}

const languageMeta: Record<string, { label: string; name: string }> = {
  vi: { label: 'VI', name: 'Tiếng Việt' },
  en: { label: 'EN', name: 'English' },
  ja: { label: 'JA', name: '日本語' },
}

export function BrandHeader({
  bootstrap,
  view,
  hasWorkspace,
  authenticated,
  globalRole,
  onNavigate,
  onLanguageChange,
  onSignOut,
}: BrandHeaderProps) {
  const { t, i18n } = useTranslation()
  const [languageOpen, setLanguageOpen] = useState(false)
  const [mobileNavOpen, setMobileNavOpen] = useState(false)
  const languageRef = useRef<HTMLDivElement | null>(null)
  const mobileNavRef = useRef<HTMLDivElement | null>(null)
  const selectedLocale = (i18n.resolvedLanguage || bootstrap.locale).split('-')[0]
  const languageOptions = Array.from(
    new Map(
      [...bootstrap.locales, { code: 'ja', label: 'JA' }].map((locale) => {
        const code = locale.code.toLowerCase()
        return [code, { code, label: languageMeta[code]?.label || locale.label.toUpperCase() }]
      }),
    ).values(),
  )
  const initials = bootstrap.session.display_name
    .split(/\s+/)
    .filter(Boolean)
    .slice(-2)
    .map((part) => part[0]?.toLocaleUpperCase())
    .join('')
  const workspaceNavigationActive = view === 'workspaces' || view === 'project'
  const assistantNavigationActive = view === 'assistant'
  const agentFlowNavigationActive = view === 'agent-flow'

  useEffect(() => {
    if (!languageOpen) return

    function closeOnOutsideClick(event: MouseEvent) {
      if (!languageRef.current?.contains(event.target as Node)) {
        setLanguageOpen(false)
      }
    }

    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') {
        setLanguageOpen(false)
      }
    }

    document.addEventListener('mousedown', closeOnOutsideClick)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('mousedown', closeOnOutsideClick)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [languageOpen])

  useEffect(() => {
    if (!mobileNavOpen) return
    function closeMobileNav(event: MouseEvent) {
      if (!mobileNavRef.current?.contains(event.target as Node)) setMobileNavOpen(false)
    }
    function closeMobileNavOnEscape(event: KeyboardEvent) {
      if (event.key === 'Escape') setMobileNavOpen(false)
    }
    document.addEventListener('mousedown', closeMobileNav)
    document.addEventListener('keydown', closeMobileNavOnEscape)
    return () => {
      document.removeEventListener('mousedown', closeMobileNav)
      document.removeEventListener('keydown', closeMobileNavOnEscape)
    }
  }, [mobileNavOpen])

  const navigateFromMobile = (nextView: ViewName) => {
    setMobileNavOpen(false)
    onNavigate(nextView)
  }
  const goToSection = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
  }

  const chooseLanguage = (locale: string) => {
    setLanguageOpen(false)
    if (locale !== selectedLocale) {
      onLanguageChange(locale)
    }
  }

  return (
    <header className={`brand-header ${view !== 'home' ? 'brand-header--workspace' : ''}`}>
      <div className="header-inner">
        <Button variant="unstyled" className="brand-mark" onClick={() => onNavigate('home')} aria-label={t('nav.home')}>
          <span className="brand-bolt" aria-hidden="true">ϟ</span>
          <span>{view === 'home' ? bootstrap.brand.name : bootstrap.brand.product.replace(' Workspace', '')}</span>
        </Button>

        {view === 'home' ? (
          <nav className="main-nav" aria-label={t('nav.landingNavigation')}>
            <Button variant="ghost" onClick={() => goToSection('features')}>{t('nav.features')}</Button>
            <Button variant="ghost" onClick={() => goToSection('platform')}>{t('nav.platform')}</Button>
            <Button variant="ghost" onClick={() => goToSection('contact')}>{t('nav.contact')}</Button>
          </nav>
        ) : (
          <>
            <nav className="main-nav workspace-top-nav" aria-label={t('nav.workspaceNavigation')}>
              <Button variant="ghost" className={view === 'dashboard' ? 'is-active' : ''} onClick={() => onNavigate('dashboard')}>{t('nav.dashboard')}</Button>
              <Button variant="ghost" className={workspaceNavigationActive ? 'is-active' : ''} onClick={() => onNavigate('workspaces')}>{t('nav.workspaces')}</Button>
              {globalRole === 'admin' && (
                <Button variant="ghost" className={view === 'admin' ? 'is-active' : ''} onClick={() => onNavigate('admin')} leadingIcon={<UserRoundCog size={15} />}>{t('nav.userAccess')}</Button>
              )}
              <Button variant="ghost" className={assistantNavigationActive ? 'is-active' : ''} disabled={!hasWorkspace} onClick={() => onNavigate('assistant')}>{t('nav.intelligence')}</Button>
              <Button variant="ghost" className={agentFlowNavigationActive ? 'is-active' : ''} disabled={!hasWorkspace} onClick={() => onNavigate('agent-flow')} leadingIcon={<GitBranch size={15} />}>Agent Flow</Button>
            </nav>
          </>
        )}

        <div className="header-actions">
          {view !== 'home' && (
            <div className="mobile-nav" ref={mobileNavRef}>
              <Button variant="ghost" size="icon" className="mobile-nav-trigger" aria-label={t('nav.mobileMenu')} aria-expanded={mobileNavOpen} onClick={() => setMobileNavOpen((open) => !open)}>
                <Menu size={18} />
              </Button>
              {mobileNavOpen && (
                <nav className="mobile-nav-popover" aria-label={t('nav.mobileMenu')}>
                  <Button variant="ghost" className={view === 'dashboard' ? 'is-active' : ''} onClick={() => navigateFromMobile('dashboard')}>{t('nav.dashboard')}</Button>
                  <Button variant="ghost" className={workspaceNavigationActive ? 'is-active' : ''} onClick={() => navigateFromMobile('workspaces')}>{t('nav.workspaces')}</Button>
                  {globalRole === 'admin' && <Button variant="ghost" className={view === 'admin' ? 'is-active' : ''} onClick={() => navigateFromMobile('admin')}>{t('nav.userAccess')}</Button>}
                  <Button variant="ghost" className={assistantNavigationActive ? 'is-active' : ''} disabled={!hasWorkspace} onClick={() => navigateFromMobile('assistant')}>{t('nav.intelligence')}</Button>
                  <Button variant="ghost" className={agentFlowNavigationActive ? 'is-active' : ''} disabled={!hasWorkspace} onClick={() => navigateFromMobile('agent-flow')} leadingIcon={<GitBranch size={15} />}>Agent Flow</Button>
                  <Button variant="ghost" className="mobile-nav-signout" onClick={onSignOut}>{t('common.signOut')}</Button>
                </nav>
              )}
            </div>
          )}
          <div className="locale-picker" ref={languageRef} data-open={languageOpen ? 'true' : 'false'}>
            <button
              type="button"
              className="locale-trigger"
              aria-haspopup="listbox"
              aria-expanded={languageOpen}
              aria-label={t('nav.language')}
              onClick={() => setLanguageOpen((open) => !open)}
            >
              <Globe2 aria-hidden="true" size={16} />
              <span>{languageMeta[selectedLocale]?.label || selectedLocale.toUpperCase()}</span>
              <ChevronDown aria-hidden="true" size={13} />
            </button>
            {languageOpen && (
              <div className="locale-menu" role="listbox" aria-label={t('nav.language')}>
                {languageOptions.map((locale) => {
                  const selected = locale.code === selectedLocale
                  return (
                    <button
                      key={locale.code}
                      type="button"
                      className={selected ? 'is-selected' : ''}
                      role="option"
                      aria-selected={selected}
                      onClick={() => chooseLanguage(locale.code)}
                    >
                      <span className="locale-option-code">{locale.label}</span>
                      <span className="locale-option-name">{languageMeta[locale.code]?.name || locale.label}</span>
                      {selected && <Check aria-hidden="true" size={14} />}
                    </button>
                  )
                })}
              </div>
            )}
          </div>
          {view === 'home' ? (
            <Button variant="primary" size="sm" shape="pill" onClick={() => onNavigate(authenticated ? 'dashboard' : 'signin')}>
              {authenticated ? t('nav.enterWorkspace') : t('nav.signIn')}
            </Button>
          ) : (
            <>
              <span className="header-icon desktop-header-action" aria-hidden="true"><Bell size={17} /></span>
              <div className="user-avatar" title={bootstrap.session.display_name}>{initials}</div>
              <Button variant="ghost" size="icon" className="desktop-header-action" aria-label={t('nav.signOut')} title={t('nav.signOut')} onClick={onSignOut}>
                <LogOut size={17} />
              </Button>
            </>
          )}
        </div>
      </div>
    </header>
  )
}
