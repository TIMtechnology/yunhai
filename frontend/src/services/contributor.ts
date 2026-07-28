const KEY = 'yunhai_contributor_id'
const API_BASE = import.meta.env.VITE_API_BASE || ''
let sessionPromise: Promise<string> | null = null

export function getContributorId(): string {
  return localStorage.getItem(KEY) || ''
}

export async function ensureContributorSession(): Promise<string> {
  if (!sessionPromise) {
    sessionPromise = fetch(`${API_BASE}/api/contribute/session`, {
      method: 'POST',
      credentials: 'include',
    })
      .then(async (resp) => {
        if (!resp.ok) throw new Error('无法建立贡献者会话')
        const data = (await resp.json()) as { contributor_id: string }
        localStorage.setItem(KEY, data.contributor_id)
        return data.contributor_id
      })
      .catch((error) => {
        sessionPromise = null
        throw error
      })
  }
  return sessionPromise
}

export function contributorIdShort(): string {
  return getContributorId().slice(-8)
}

export function contributorHeaders(): HeadersInit {
  return {
    'Content-Type': 'application/json',
  }
}
