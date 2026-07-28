import { gzipSync } from 'node:zlib'
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

const assetDir = new URL('../dist/assets/', import.meta.url)
const budgets = {
  main: 60 * 1024,
  label: 25 * 1024,
  echarts: 600 * 1024,
}

for (const [prefix, limit] of Object.entries(budgets)) {
  const file = readdirSync(assetDir).find(
    (name) => name.startsWith(`${prefix}-`) && name.endsWith('.js'),
  )
  if (!file) {
    throw new Error(`bundle budget: missing ${prefix} JavaScript asset`)
  }
  const size = gzipSync(readFileSync(join(assetDir.pathname, file))).byteLength
  if (size > limit) {
    throw new Error(
      `bundle budget exceeded: ${file} is ${(size / 1024).toFixed(1)} KiB gzip, limit ${(limit / 1024).toFixed(0)} KiB`,
    )
  }
  console.log(`bundle budget: ${file} ${(size / 1024).toFixed(1)} KiB gzip`)
}
