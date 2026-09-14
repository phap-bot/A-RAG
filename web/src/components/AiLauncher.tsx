import { Sparkles } from 'lucide-react'
import { useTranslation } from 'react-i18next'

import { Button } from './ui/Button'

type AiLauncherProps = { onOpen: () => void }

export function AiLauncher({ onOpen }: AiLauncherProps) {
  const { t } = useTranslation()
  return (
    <Button
      variant="secondary"
      shape="pill"
      className="ai-launcher"
      leadingIcon={<span className="ai-dot" aria-hidden="true"><Sparkles size={15} strokeWidth={2.2} /></span>}
      onClick={onOpen}
    >
      {t('assistant.launcher')}
    </Button>
  )
}
