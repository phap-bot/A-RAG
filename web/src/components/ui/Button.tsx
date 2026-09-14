import { LoaderCircle } from 'lucide-react'
import { forwardRef, type ButtonHTMLAttributes, type ReactNode } from 'react'

type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'unstyled'
type ButtonSize = 'sm' | 'md' | 'lg' | 'icon'
type ButtonShape = 'rounded' | 'pill' | 'square'

export type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant
  size?: ButtonSize
  shape?: ButtonShape
  loading?: boolean
  leadingIcon?: ReactNode
  trailingIcon?: ReactNode
  fullWidth?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    children,
    className = '',
    variant = 'secondary',
    size = 'md',
    shape = 'rounded',
    loading = false,
    leadingIcon,
    trailingIcon,
    fullWidth = false,
    disabled,
    type = 'button',
    ...props
  },
  ref,
) {
  const classes = [
    'ui-button',
    `ui-button--${variant}`,
    `ui-button--${size}`,
    `ui-button--${shape}`,
    fullWidth ? 'ui-button--full' : '',
    loading ? 'is-loading' : '',
    className,
  ].filter(Boolean).join(' ')

  return (
    <button
      ref={ref}
      className={classes}
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? <LoaderCircle className="ui-button__spinner" aria-hidden="true" /> : leadingIcon}
      {children}
      {!loading && trailingIcon}
    </button>
  )
})
