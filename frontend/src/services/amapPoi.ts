import type { SpotSearchResult } from './api'
import { loadAmap } from './amap'

interface AmapPoiRaw {
  id?: string
  name?: string
  address?: string
  location?: { lng: number; lat: number } | string
  pname?: string
  cityname?: string
  adname?: string
  type?: string
}

const REGION_HINT =
  /(省|市|县|区|自治州|盟)|^(北京|上海|天津|重庆|河南|河北|辽宁|山东|四川|湖北|湖南|江西|浙江|江苏|广东|广西|云南|贵州|陕西|甘肃|青海|海南|台湾|内蒙古|黑龙江|吉林|新疆|宁夏|西藏)/

function parseLocation(loc: AmapPoiRaw['location']): { lng: number; lat: number } | null {
  if (!loc) return null
  if (typeof loc === 'string' && loc.includes(',')) {
    const [lngStr, latStr] = loc.split(',', 2)
    const lng = Number(lngStr)
    const lat = Number(latStr)
    if (Number.isNaN(lng) || Number.isNaN(lat)) return null
    return { lng, lat }
  }
  if (typeof loc === 'object' && typeof loc.lng === 'number' && typeof loc.lat === 'number') {
    return { lng: loc.lng, lat: loc.lat }
  }
  return null
}

function mapPoi(poi: AmapPoiRaw, query: string): SpotSearchResult | null {
  const pos = parseLocation(poi.location)
  if (!pos) return null
  const region = `${poi.pname || ''}${poi.cityname || ''}${poi.adname || ''}`.trim()
  return {
    id: `poi-${poi.id || `${pos.lng.toFixed(4)}-${pos.lat.toFixed(4)}`}`,
    name: poi.name || query,
    region: region || poi.address || '',
    source: 'amap',
    lat: pos.lat,
    lng: pos.lng,
    address: poi.address || undefined,
  }
}

function rankPoi(item: SpotSearchResult, query: string): number {
  const text = `${item.name} ${item.address || ''} ${item.region || ''}`
  let score = 0
  if (/风景区|风景名胜区|景区|国家公园|森林公园|地质公园/.test(text)) score += 120
  if (/游客中心|售票|观景台|主峰|索道|玻璃栈|日出/.test(text)) score += 40
  if (item.name.includes(query)) score += 25
  if (/政府|委员会|人大|纪检|公司|学校|医院|加油|税务|商会|事务/.test(item.name)) score -= 50
  return score
}

function buildKeywords(q: string): string[] {
  const keywords = new Set<string>([q])
  if (!/风景区|景区/.test(q)) keywords.add(`${q}风景区`)
  if (q.endsWith('山') && q.length >= 2) keywords.add(`${q}景区`)
  return [...keywords]
}

function placeSearchOnce(
  keyword: string,
  options?: { city?: string; count?: number },
): Promise<AmapPoiRaw[]> {
  return new Promise((resolve, reject) => {
    window.AMap.plugin('AMap.PlaceSearch', () => {
      const ps = new window.AMap.PlaceSearch({
        pageSize: options?.count ?? 12,
        city: options?.city || '全国',
        citylimit: Boolean(options?.city),
        pageIndex: 1,
      })
      ps.search(keyword, (status: string, result: any) => {
        if (status === 'complete' || status === 'OK') {
          resolve(result?.poiList?.pois ?? [])
          return
        }
        if (status === 'no_data') {
          resolve([])
          return
        }
        reject(new Error('高德 POI 搜索失败'))
      })
    })
  })
}

/** 浏览器端调用高德 PlaceSearch（GCJ-02 坐标）。 */
export async function searchAmapPoi(
  query: string,
  options?: { lat?: number; lng?: number; count?: number; regionalBias?: boolean },
): Promise<SpotSearchResult[]> {
  const q = query.trim()
  if (!q) return []

  await loadAmap()
  const count = options?.count ?? 12
  const useRegional =
    (options?.regionalBias ?? false) &&
    options?.lat != null &&
    options?.lng != null &&
    !REGION_HINT.test(q)

  const keywords = buildKeywords(q)
  const seen = new Set<string>()
  const merged: SpotSearchResult[] = []
  let lastError: Error | null = null

  for (const keyword of keywords) {
    try {
      const pois = await placeSearchOnce(keyword, {
        count,
        city: useRegional ? '全国' : undefined,
      })
      for (const raw of pois) {
        const item = mapPoi(raw, q)
        if (!item || seen.has(item.id)) continue
        seen.add(item.id)
        merged.push(item)
      }
    } catch (err) {
      lastError = err instanceof Error ? err : new Error(String(err))
    }
  }

  if (!merged.length && lastError) throw lastError

  return merged.sort((a, b) => rankPoi(b, q) - rankPoi(a, q)).slice(0, count)
}
