import { useMemo, useState, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ArrowLeft,
  ArrowRight,
  CheckCircle2,
  Eye,
  EyeOff,
  KeyRound,
  LockKeyhole,
  Mail,
  ShieldCheck,
  Sparkles,
  UserRound,
  Zap,
} from 'lucide-react'

import { requestPasswordReset, resetPassword, signIn, signUp } from '../api'
import { Button } from '../components/ui/Button'
import type { AuthUser } from '../types'

export type AuthMode = 'signin' | 'signup' | 'forgot' | 'reset'

type AuthPageProps = {
  mode: AuthMode
  resetToken?: string
  onModeChange: (mode: AuthMode) => void
  onSuccess: (user: AuthUser) => void
  onBack: () => void
}

export function AuthPage({ mode, resetToken = '', onModeChange, onSuccess, onBack }: AuthPageProps) {
  const { t } = useTranslation()
  const [displayName, setDisplayName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [remember, setRemember] = useState(false)
  const [accepted, setAccepted] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const isSignup = mode === 'signup'
  const isSignin = mode === 'signin'
  const isForgot = mode === 'forgot'
  const isReset = mode === 'reset'

  const passwordStrength = useMemo(() => {
    if (!password) return 0
    return Math.min(4, [password.length >= 12, /[A-Z]/.test(password), /[0-9]/.test(password), /[^A-Za-z0-9]/.test(password)].filter(Boolean).length)
  }, [password])

  const heading = isSignup ? t('auth.signupHeading') : isForgot ? t('auth.forgotHeading') : isReset ? t('auth.resetHeading') : t('auth.signinHeading')
  const description = isSignup ? t('auth.signupDescription') : isForgot ? t('auth.forgotDescription') : isReset ? t('auth.resetDescription') : t('auth.signinDescription')
  const storyEyebrow = isSignup ? t('auth.signupEyebrow') : isForgot || isReset ? t('auth.recoveryEyebrow') : t('auth.signinEyebrow')
  const storyBody = isSignup ? t('auth.signupStoryBody') : isForgot || isReset ? t('auth.recoveryStoryBody') : t('auth.signinStoryBody')
  const switchText = isSignup ? t('auth.hasAccount') : isSignin ? t('auth.noAccount') : t('auth.backQuestion')
  const switchAction = isSignup || isForgot || isReset ? t('auth.submitSignin') : t('auth.signupLink')
  const submitLabel = isSignup ? t('auth.submitSignup') : isForgot ? t('auth.submitForgot') : isReset ? t('auth.submitReset') : t('auth.submitSignin')

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSubmitting(true)
    setError(null)
    setSuccess(null)
    try {
      if (isForgot) {
        const result = await requestPasswordReset(email)
        setSuccess(result.message)
      } else if (isReset) {
        if (!resetToken) throw new Error(t('auth.invalidReset'))
        await resetPassword(resetToken, password)
        setSuccess(t('auth.resetSuccess'))
      } else {
        const result = isSignup
          ? await signUp({ display_name: displayName, email, password, accept_terms: accepted })
          : await signIn({ email, password, remember })
        onSuccess(result.user)
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t('auth.backendError'))
    } finally {
      setPassword('')
      setSubmitting(false)
    }
  }

  return (
    <section className="auth-page">
      <div className="auth-grid" aria-hidden="true" />
      <div className="auth-orb auth-orb--one" aria-hidden="true" />
      <div className="auth-orb auth-orb--two" aria-hidden="true" />
      <Button variant="ghost" className="auth-back" leadingIcon={<ArrowLeft />} onClick={onBack}>{t('auth.home')}</Button>

      <div className="auth-shell">
        <aside className="auth-story">
          <div className="auth-brand"><span>ϟ</span><strong>AI Document</strong></div>
          <div className="auth-story-copy">
            <small>{storyEyebrow}</small>
            <h1>
              {isSignup ? <>{t('auth.signupStoryTitle')}<br /><em>{t('auth.signupStoryAccent')}</em></> : isForgot || isReset ? <>{t('auth.recoveryStoryTitle')}<br /><em>{t('auth.recoveryStoryAccent')}</em></> : <>{t('auth.signinStoryTitle')}<br />{t('auth.signinStoryMid')}<br /><em>{t('auth.signinStoryAccent')}</em></>}
            </h1>
            <p>{storyBody}</p>
          </div>
          <div className="auth-trust-list">
            <div><ShieldCheck /><span><strong>{t('auth.multilayerSecurity')}</strong><small>{t('auth.securityDetail')}</small></span></div>
            <div>{isForgot || isReset ? <KeyRound /> : <Zap />}<span><strong>{isForgot || isReset ? t('auth.oneTimeToken') : t('auth.instantAccess')}</strong><small>{isForgot || isReset ? t('auth.recoveryTokenProtected') : t('auth.backendSession')}</small></span></div>
          </div>
          <span className="auth-copyright">{t('auth.copyright')}</span>
        </aside>

        <div className="auth-form-panel">
          <div className="auth-form-heading">
            <span><Sparkles /> {isForgot || isReset ? t('auth.passwordRecovery') : t('auth.secureAccess')}</span>
            <h2>{heading}</h2>
            <p>{description}</p>
          </div>
          {success ? (
            <div className="auth-success" role="status">
              <CheckCircle2 />
              <strong>{t('auth.successTitle')}</strong>
              <p>{success}</p>
              {isReset && <Button variant="primary" fullWidth onClick={() => onModeChange('signin')}>{t('auth.backToSignin')}</Button>}
            </div>
          ) : (
            <form className="auth-form" onSubmit={submit}>
              {isSignup && <div className="auth-field"><label htmlFor="auth-display-name">{t('auth.displayName')}</label><div><UserRound /><input id="auth-display-name" autoComplete="name" value={displayName} onChange={(event) => setDisplayName(event.target.value)} placeholder={t('auth.namePlaceholder')} minLength={2} maxLength={96} required /></div></div>}
              {!isReset && <div className="auth-field"><label htmlFor="auth-email">{t('auth.email')}</label><div><Mail /><input id="auth-email" type="email" autoComplete="email" value={email} onChange={(event) => setEmail(event.target.value)} placeholder={t('auth.emailPlaceholder')} maxLength={254} required /></div></div>}
              {!isForgot && <div className="auth-field"><div className="auth-label-row"><label htmlFor="auth-password">{t('auth.password')}</label>{isSignin && <button type="button" aria-label={t('auth.forgotPassword')} onClick={() => onModeChange('forgot')}>{t('auth.forgotPassword')}</button>}</div><div><LockKeyhole /><input id="auth-password" type={showPassword ? 'text' : 'password'} autoComplete={isSignin ? 'current-password' : 'new-password'} value={password} onChange={(event) => setPassword(event.target.value)} placeholder={t('auth.passwordPlaceholder')} minLength={12} maxLength={128} required /><button type="button" aria-label={showPassword ? t('auth.hidePassword') : t('auth.showPassword')} onClick={() => setShowPassword((value) => !value)}>{showPassword ? <EyeOff /> : <Eye />}</button></div></div>}
              {(isSignup || isReset) && <div className="password-strength" aria-label={t('auth.passwordStrength', { score: passwordStrength })}>{[1, 2, 3, 4].map((level) => <i key={level} className={passwordStrength >= level ? 'is-active' : ''} />)}<span>{t('auth.passwordHint')}</span></div>}
              {(isSignup || isSignin) && <label className="auth-check"><input type="checkbox" checked={isSignup ? accepted : remember} onChange={(event) => isSignup ? setAccepted(event.target.checked) : setRemember(event.target.checked)} required={isSignup} /><span>{isSignup ? t('auth.acceptTerms') : t('auth.remember')}</span></label>}
              {error && <div className="auth-error" role="alert">{error}</div>}
              <Button type="submit" variant="primary" fullWidth loading={submitting} trailingIcon={<ArrowRight />}>{submitLabel}</Button>
            </form>
          )}
          <p className="auth-switch">{switchText}<button type="button" onClick={() => onModeChange(isSignup || isForgot || isReset ? 'signin' : 'signup')}>{switchAction}</button></p>
        </div>
      </div>
    </section>
  )
}
