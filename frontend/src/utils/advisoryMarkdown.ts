function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function formatInline(s: string): string {
  return escapeHtml(s).replace(/\*\*(.+?)\*\*/g, '<strong class="font-semibold text-indigo-50">$1</strong>')
}

function isTableRow(line: string): boolean {
  const t = line.trim()
  return t.startsWith('|') && t.includes('|', 1)
}

function isTableSeparator(line: string): boolean {
  const t = line.trim().replace(/^\|/, '').replace(/\|$/, '')
  const cells = t.split('|').map((c) => c.trim())
  if (!cells.length) return false
  return cells.every((c) => /^:?-{2,}:?$/.test(c))
}

function parseTableCells(line: string): string[] {
  const t = line.trim()
  const inner = t.startsWith('|') ? t.slice(1) : t
  const trimmed = inner.endsWith('|') ? inner.slice(0, -1) : inner
  return trimmed.split('|').map((c) => c.trim())
}

function renderTable(rows: string[]): string {
  if (!rows.length) return ''
  let header = parseTableCells(rows[0])
  let bodyStart = 1
  if (rows.length > 1 && isTableSeparator(rows[1])) {
    bodyStart = 2
  }
  const bodyRows = rows.slice(bodyStart).map(parseTableCells)
  const colCount = Math.max(header.length, ...bodyRows.map((r) => r.length))
  header = Array.from({ length: colCount }, (_, i) => header[i] ?? '')

  const headHtml = header
    .map((c) => `<th class="advisory-th">${formatInline(c)}</th>`)
    .join('')
  const bodyHtml = bodyRows
    .map((row) => {
      const cells = Array.from({ length: colCount }, (_, i) => row[i] ?? '')
      return `<tr>${cells.map((c) => `<td class="advisory-td">${formatInline(c)}</td>`).join('')}</tr>`
    })
    .join('')

  return `<div class="advisory-table-wrap my-1.5 overflow-x-auto"><table class="advisory-table"><thead><tr>${headHtml}</tr></thead><tbody>${bodyHtml}</tbody></table></div>`
}

/** 将 AI 解读 Markdown（标题、加粗、列表、表格）转为安全 HTML */
export function renderAdvisoryMarkdown(raw: string): string {
  const lines = raw.trim().split('\n')
  const parts: string[] = []
  let listItems: string[] = []
  let paraLines: string[] = []
  let tableRows: string[] = []

  const flushList = () => {
    if (!listItems.length) return
    parts.push(
      `<ul class="advisory-ul my-1 list-disc space-y-0.5 pl-4">${listItems
        .map((li) => `<li>${formatInline(li)}</li>`)
        .join('')}</ul>`,
    )
    listItems = []
  }

  const flushTable = () => {
    if (!tableRows.length) return
    parts.push(renderTable(tableRows))
    tableRows = []
  }

  const flushPara = () => {
    flushList()
    flushTable()
    if (!paraLines.length) return
    const text = paraLines.join(' ').trim()
    if (text) parts.push(`<p class="advisory-p my-1">${formatInline(text)}</p>`)
    paraLines = []
  }

  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed) {
      flushPara()
      continue
    }

    if (isTableRow(trimmed)) {
      flushList()
      paraLines = []
      tableRows.push(trimmed)
      continue
    }

    if (tableRows.length) {
      flushTable()
    }

    if (trimmed.startsWith('- ') || trimmed.startsWith('• ') || trimmed.startsWith('* ')) {
      flushPara()
      const bullet = trimmed.startsWith('- ') ? 2 : 2
      listItems.push(trimmed.slice(bullet))
      continue
    }

    flushList()
    if (trimmed.startsWith('## ')) {
      flushPara()
      parts.push(`<h4 class="advisory-h4">${formatInline(trimmed.slice(3))}</h4>`)
    } else if (trimmed.startsWith('### ')) {
      flushPara()
      parts.push(`<h5 class="advisory-h5">${formatInline(trimmed.slice(4))}</h5>`)
    } else {
      paraLines.push(trimmed)
    }
  }
  flushPara()
  return parts.join('')
}
