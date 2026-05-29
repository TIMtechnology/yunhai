const KEY = 'yunhai_contributor_id'

export function getContributorId(): string {
  let id = localStorage.getItem(KEY)
  if (!id) {
    id = `cid_${crypto.randomUUID()}`
    localStorage.setItem(KEY, id)
  }
  return id
}

export function contributorIdShort(): string {
  return getContributorId().slice(-8)
}

export function contributorHeaders(): HeadersInit {
  return {
    'Content-Type': 'application/json',
    'X-Contributor-Id': getContributorId(),
  }
}
