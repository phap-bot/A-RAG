import {
  ArrowRight,
  Bot,
  Braces,
  CheckCircle2,
  FileSearch,
  FileText,
  Network,
  Search,
  ShieldCheck,
  Sparkles,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from '../components/ui/Button'
import type { UiBootstrap, WorkspaceRecord } from '../types'

type HomePageProps = {
  bootstrap: UiBootstrap
  workspaces: WorkspaceRecord[]
  onContinue: () => void
}

const featureIcons = {
  'semantic-search': Search,
  'hybrid-retrieval': Network,
  analysis: Bot,
  security: ShieldCheck,
}

export function HomePage({ bootstrap, workspaces, onContinue }: HomePageProps) {
  const { t, i18n } = useTranslation()
  const totalDocuments = workspaces.reduce((total, workspace) => total + workspace.document_count, 0)
  const capabilities = bootstrap.capabilities
  const localeMap: Record<string, string> = { en: 'en-US', ja: 'ja-JP', vi: 'vi-VN' }
  const locale = localeMap[(i18n.resolvedLanguage || 'vi').split('-')[0]] || 'vi-VN'
  const statusOn = t('status.on')
  const statusOff = t('status.off')

  return (
    <div className="page page-home">
      <div className="landing-mesh" aria-hidden="true" />
      <section className="landing-hero" aria-labelledby="home-title">
        <div className="hero-orb hero-orb--left" aria-hidden="true" />
        <div className="hero-orb hero-orb--right" aria-hidden="true" />
        <div className="hero-space-field" aria-hidden="true">
          <span className="space-grid-fragment space-grid-fragment--left" />
          <span className="space-grid-fragment space-grid-fragment--center" />
          <span className="space-grid-fragment space-grid-fragment--right" />
        </div>
        <div className="hero-copy">
          <span className="hero-eyebrow"><Sparkles size={15} />{t('landing.eyebrow', { defaultValue: bootstrap.landing.eyebrow })}</span>
          <h1 id="home-title">
            <span>{t('landing.headline', { defaultValue: bootstrap.landing.headline })}</span>
            <em>{t('landing.headlineAccent', { defaultValue: bootstrap.landing.headline_accent })}</em>
          </h1>
          <p>{t('landing.description', { defaultValue: bootstrap.landing.description })}</p>
          <div className="hero-actions">
            <Button variant="primary" size="lg" shape="pill" trailingIcon={<ArrowRight size={18} />} onClick={onContinue}>
              {t('landing.primaryAction', { defaultValue: bootstrap.landing.primary_action })}
            </Button>
            <Button variant="secondary" size="lg" shape="pill" onClick={onContinue}>
              {t('landing.secondaryAction', { defaultValue: bootstrap.landing.secondary_action })}
            </Button>
          </div>
        </div>

        <div className="hero-product-frame" id="platform" aria-label={t('landing.productFrameLabel')}>
          <div className="product-window-bar">
            <span /><span /><span />
            <small>{bootstrap.brand.product}</small>
          </div>
          <div className="product-window-body">
            <aside>
              <strong><Braces size={16} /> {t('landing.product.workspaceName')}</strong>
              <span className="is-active">{t('landing.product.overview')}</span>
              <span>{t('landing.product.documents')}</span>
              <span>{t('landing.product.insights')}</span>
            </aside>
            <div className="product-window-content">
              <div className="preview-heading">
                <div><small>{t('landing.product.liveData')}</small><strong>{t('landing.product.overviewTitle')}</strong></div>
                <span>{t('landing.product.workspaceCount', { count: workspaces.length })}</span>
              </div>
              <div className="preview-cards">
                {workspaces.slice(0, 3).map((workspace, index) => (
                  <article key={workspace.workspace_id}>
                    <i>{index % 2 ? <FileSearch size={18} /> : <FileText size={18} />}</i>
                    <strong>{workspace.name}</strong>
                    <span>{t('landing.product.documentCount', { count: workspace.document_count })}</span>
                    <small data-status={workspace.status}>{workspace.status}</small>
                  </article>
                ))}
                {!workspaces.length && <div className="preview-empty">{t('landing.product.empty')}</div>}
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="landing-features" id="features" aria-labelledby="features-title">
        <div className="landing-section-heading">
          <span>{t('landing.sectionLabel')}</span>
          <h2 id="features-title">{t('landing.featuresTitle', { defaultValue: bootstrap.landing.features_title })}</h2>
          <p>{t('landing.featuresDescription', { defaultValue: bootstrap.landing.features_description })}</p>
        </div>
        <div className="feature-bento">
          {bootstrap.landing.features.map((feature, index) => {
            const Icon = featureIcons[feature.id as keyof typeof featureIcons] || Sparkles
            return (
              <article className={`feature-card feature-card--${index + 1}`} key={feature.id}>
                <i><Icon size={23} /></i>
                <h3>{t(`landing.features.${feature.id}.title`, { defaultValue: feature.title })}</h3>
                <p>{t(`landing.features.${feature.id}.description`, { defaultValue: feature.description })}</p>
                {index === 0 && (
                  <div className="neural-visual" aria-hidden="true">
                    <span /><span /><span /><span /><span />
                    <Search size={30} />
                  </div>
                )}
                {index === 3 && (
                  <div className="security-points">
                    <span><CheckCircle2 size={15} /> {t('landing.securityPoints.session')}</span>
                    <span><CheckCircle2 size={15} /> {t('landing.securityPoints.replay')}</span>
                  </div>
                )}
              </article>
            )
          })}
        </div>
      </section>

      <section className="landing-stats" aria-label={t('landing.statsLabel')}>
        <article><strong>{workspaces.length}</strong><span>{t('landing.stats.workspaces')}</span></article>
        <article><strong>{totalDocuments.toLocaleString(locale)}</strong><span>{t('landing.stats.documents')}</span></article>
        <article><strong>{capabilities.bm25_enabled ? statusOn : statusOff}</strong><span>{t('landing.stats.precision')}</span></article>
        <article><strong>{capabilities.rerank_enabled ? statusOn : statusOff}</strong><span>{t('landing.stats.suggestionQuality')}</span></article>
      </section>

      <section className="landing-cta" id="contact">
        <div>
          <Sparkles size={24} />
          <h2>{t('landing.cta.title')}</h2>
          <p>{t('landing.cta.description')}</p>
          <Button variant="primary" size="lg" shape="pill" trailingIcon={<ArrowRight size={18} />} onClick={onContinue}>
            {t('landing.primaryAction', { defaultValue: bootstrap.landing.primary_action })}
          </Button>
        </div>
      </section>

      <footer className="landing-footer">
        <div><strong>{bootstrap.brand.name}</strong><span>{bootstrap.brand.product}</span></div>
        <span>{t('landing.footer.security')}</span>
      </footer>
    </div>
  )
}