import { Check, ChevronDown } from 'lucide-react'
import { type KeyboardEvent, useEffect, useId, useRef, useState } from 'react'

export type SelectOption = {
  value: string
  label: string
  disabled?: boolean
}

type SelectProps = {
  value: string
  options: SelectOption[]
  onChange: (value: string) => void
  ariaLabel?: string
  placeholder?: string
  disabled?: boolean
  className?: string
}

export function Select({
  value,
  options,
  onChange,
  ariaLabel,
  placeholder = 'Select an option',
  disabled = false,
  className = '',
}: SelectProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const menuId = useId()
  const [open, setOpen] = useState(false)
  const selected = options.find((option) => option.value === value)
  const enabledOptions = options.filter((option) => !option.disabled)
  const [activeValue, setActiveValue] = useState(selected?.value || enabledOptions[0]?.value || '')

  useEffect(() => {
    if (!open) return
    setActiveValue(selected?.value || enabledOptions[0]?.value || '')
  }, [open, selected?.value])

  useEffect(() => {
    if (!open) return

    function handlePointerDown(event: PointerEvent) {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false)
    }

    document.addEventListener('pointerdown', handlePointerDown)
    return () => document.removeEventListener('pointerdown', handlePointerDown)
  }, [open])

  function choose(option: SelectOption) {
    if (option.disabled) return
    onChange(option.value)
    setActiveValue(option.value)
    setOpen(false)
  }

  function moveActive(direction: 1 | -1) {
    if (!enabledOptions.length) return
    const currentIndex = Math.max(0, enabledOptions.findIndex((option) => option.value === activeValue))
    const nextIndex = (currentIndex + direction + enabledOptions.length) % enabledOptions.length
    setActiveValue(enabledOptions[nextIndex].value)
  }

  function handleKeyDown(event: KeyboardEvent<HTMLButtonElement>) {
    if (disabled) return
    if (event.key === 'Escape') {
      setOpen(false)
      return
    }
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      if (!open) setOpen(true)
      moveActive(event.key === 'ArrowDown' ? 1 : -1)
      return
    }
    if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault()
      if (!open) setOpen(true)
      setActiveValue((event.key === 'Home' ? enabledOptions[0] : enabledOptions[enabledOptions.length - 1])?.value || '')
      return
    }
    if ((event.key === 'Enter' || event.key === ' ') && open) {
      event.preventDefault()
      const active = enabledOptions.find((option) => option.value === activeValue)
      if (active) choose(active)
    }
  }

  return (
    <div ref={rootRef} className={`ui-select ${className}`.trim()} data-open={open}>
      <button
        type="button"
        className="ui-select__trigger"
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={menuId}
        disabled={disabled}
        onClick={() => setOpen((current) => !current)}
        onKeyDown={handleKeyDown}
      >
        <span>{selected?.label || placeholder}</span>
        <ChevronDown aria-hidden="true" />
      </button>

      {open && (
        <div id={menuId} className="ui-select__menu" role="listbox" aria-label={ariaLabel}>
          {options.map((option) => (
            <button
              type="button"
              role="option"
              aria-selected={option.value === value}
              disabled={option.disabled}
              className={[
                'ui-select__option',
                option.value === value ? 'is-selected' : '',
                option.value === activeValue ? 'is-active' : '',
              ].filter(Boolean).join(' ')}
              key={option.value}
              onMouseEnter={() => setActiveValue(option.value)}
              onClick={() => choose(option)}
            >
              <span>{option.label}</span>
              {option.value === value && <Check aria-hidden="true" />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
