import { TIANDITU_KEY } from '../config'
import type { SpotSearchResult } from './api'

const SEARCH_URL = 'https://api.tianditu.gov.cn/v2/search'

interface TiandituPoiRaw {
  name?: string
  address?: string
  lonlat?: string
  hotPointID?: string
  poiType?: string
  province?: string
  city?: string
  county?: string
}

function mapBound(centerLat?: number, centerLng?: number): string {
  if (centerLat == null || centerLng == null) {
    return '73.66,3.86,135.05,53.55'
  }
  const west = Math.max(73.66, centerLng - 2.0)
  const east = Math.min(135.05, centerLng + 2.0)
  const south = Math.max(3.86, centerLat - 2.0)
  const north = Math.min(53.55, centerLat + 2.0)
  return `${west.toFixed(4)},${south.toFixed(4)},${east.toFixed(4)},${north.toFixed(4)}`
}

/** 关键词已含地名时不用地图中心裁切，避免「焦作云台山」被五女山坐标带偏 */
const REGION_HINT =
  /(省|市|县|区|自治州|盟)|^(北京|上海|天津|重庆|河南|河北|辽宁|山东|四川|湖北|湖南|江西|浙江|江苏|广东|广西|云南|贵州|陕西|甘肃|青海|海南|台湾|内蒙古|黑龙江|吉林|新疆|宁夏|西藏|焦作|大连|本溪|黄山|九江|峨眉|泰山|华山|五台山|庐山|修武|沁阳|博爱)/

function resolveSearchBound(
  query: string,
  lat?: number,
  lng?: number,
  regionalBias = false,
): string {
  if (REGION_HINT.test(query.trim())) {
    return mapBound()
  }
  if (regionalBias && lat != null && lng != null) {
    return mapBound(lat, lng)
  }
  return mapBound()
}

function parsePoi(poi: TiandituPoiRaw, query: string): SpotSearchResult | null {
  const lonlat = poi.lonlat
  if (!lonlat || !lonlat.includes(',')) return null
  const [lngStr, latStr] = lonlat.split(',', 2)
  const lng = Number(lngStr)
  const lat = Number(latStr)
  if (Number.isNaN(lng) || Number.isNaN(lat)) return null

  const province = poi.province || ''
  const city = poi.city || ''
  const county = poi.county || ''
  const address = poi.address || ''
  const region = `${province}${city}${county}`.trim() || address

  return {
    id: `poi-${poi.hotPointID || `${lng.toFixed(4)}-${lat.toFixed(4)}`}`,
    name: poi.name || query,
    region,
    source: 'tianditu',
    lat,
    lng,
    address: address || undefined,
  }
}

/** 景区/观景点优先，行政/商业设施降权 */
function rankPoi(item: SpotSearchResult, query: string): number {
  const text = `${item.name} ${item.address || ''} ${item.region || ''}`
  let score = 0
  if (/风景区|风景名胜区|景区|国家公园|森林公园|地质公园/.test(text)) score += 120
  if (/游客中心|售票|观景台|主峰|索道|玻璃栈/.test(text)) score += 40
  if (item.name.includes(query)) score += 25
  if (query.includes('云台山') && /焦作|修武|茱萸|河南/.test(text)) score += 90
  if (query.includes('云台山') && item.name === '云台') score -= 100
  if (/政府|委员会|人大|纪检|公司|学校|医院|加油|税务|商会|事务/.test(item.name)) score -= 50
  if (/有限公司|有限责任公司/.test(item.name)) score -= 40
  return score
}

function buildKeywords(q: string): string[] {
  const keywords = new Set<string>([q])
  if (!/风景区|景区/.test(q)) keywords.add(`${q}风景区`)
  if (/云台山/.test(q) || q === '云台') {
    keywords.add('云台山')
    keywords.add('云台山风景名胜区')
    keywords.add('焦作云台山')
  }
  if (q.endsWith('山') && q.length >= 2) keywords.add(`${q}景区`)
  return [...keywords]
}

function parseTiandituError(data: Record<string, unknown>): string | null {
  const code = data.code as number | undefined
  if (code != null && code !== 0) {
    return (data.msg as string) || (data.resolve as string) || '天地图 POI 权限或参数错误'
  }
  const status = data.status
  if (status && typeof status === 'object') {
    const infocode = (status as { infocode?: number }).infocode
    if (infocode != null && infocode !== 1000) {
      return (status as { cndesc?: string }).cndesc || '天地图 POI 搜索失败'
    }
    return null
  }
  if (status != null && status !== '0' && status !== 0) {
    return '天地图 POI 搜索失败'
  }
  return null
}

async function fetchPois(keyword: string, bound: string, count: number): Promise<TiandituPoiRaw[]> {
  const postStr = JSON.stringify({
    keyWord: keyword,
    level: '12',
    mapBound: bound,
    queryType: '1',
    start: '0',
    count: String(count),
  })

  const url = `${SEARCH_URL}?postStr=${encodeURIComponent(postStr)}&type=query&tk=${TIANDITU_KEY}`
  const resp = await fetch(url)
  if (!resp.ok) {
    throw new Error(`天地图 POI 请求失败 (${resp.status})`)
  }

  const data = (await resp.json()) as Record<string, unknown>
  const err = parseTiandituError(data)
  if (err) throw new Error(err)

  return (data.pois as TiandituPoiRaw[] | undefined) ?? []
}

/**
 * 浏览器端调用天地图 POI（当前 Key 为浏览器权限，后端无法直连）。
 */
export async function searchTiandituPoi(
  query: string,
  options?: { lat?: number; lng?: number; count?: number; regionalBias?: boolean },
): Promise<SpotSearchResult[]> {
  const q = query.trim()
  if (!q) return []

  const count = options?.count ?? 12
  const bound = resolveSearchBound(q, options?.lat, options?.lng, options?.regionalBias ?? false)

  const keywords = buildKeywords(q)

  const seen = new Set<string>()
  const merged: SpotSearchResult[] = []
  let lastError: Error | null = null

  for (const keyword of keywords) {
    try {
      const pois = await fetchPois(keyword, bound, count)
      for (const raw of pois) {
        const item = parsePoi(raw, q)
        if (!item || seen.has(item.id)) continue
        seen.add(item.id)
        merged.push(item)
      }
    } catch (err) {
      lastError = err instanceof Error ? err : new Error(String(err))
    }
  }

  if (!merged.length && lastError) throw lastError

  return merged
    .sort((a, b) => rankPoi(b, q) - rankPoi(a, q))
    .slice(0, count)
}
