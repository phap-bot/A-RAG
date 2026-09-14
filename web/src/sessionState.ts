const SESSION_PREFIX = 'ai-document.session.v1'

function storageAvailable(): boolean {
  return typeof window !== 'undefined' && Boolean(window.localStorage)
}

export function sessionStorageKey(scope: string, identity: string): string {
  return `${SESSION_PREFIX}.${scope}.${encodeURIComponent(identity)}`
}

export function readSessionState<T>(key: string): T | null {
  if (!storageAvailable()) return null
  try {
    const raw = window.localStorage.getItem(key)
    return raw ? JSON.parse(raw) as T : null
  } catch {
    return null
  }
}

export function writeSessionState<T>(key: string, value: T): void {
  if (!storageAvailable()) return
  try {
    window.localStorage.setItem(key, JSON.stringify(value))
  } catch {
    // A full or blocked browser store must not break the application session.
  }
}

export function removeSessionState(key: string): void {
  if (!storageAvailable()) return
  try {
    window.localStorage.removeItem(key)
  } catch {
    // Ignore storage failures; server state remains authoritative.
  }
}
