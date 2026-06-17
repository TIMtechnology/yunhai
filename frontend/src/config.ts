export const AMAP_KEY = import.meta.env.VITE_AMAP_KEY ?? ''
export const AMAP_SECURITY = import.meta.env.VITE_AMAP_SECURITY ?? ''

/** 开源仓库（README 与页头入口保持一致） */
export const GITHUB_REPO_URL = 'https://github.com/TIMtechnology/yunhai'

export const GRADE_COLORS: Record<string, string> = {
  '极佳': '#34D399',
  '良好': '#38BDF8',
  '一般': '#FBBF24',
  '较差': '#FB923C',
  '不宜': '#F87171',
}

export const CLOUDSEA_COLOR = '#38BDF8'
export const SUNRISE_COLOR = '#FB923C'

/** 预报天数（Open-Meteo 免费层最多 16 天，5 天精度较稳定） */
export const FORECAST_DAYS = 5
export const FORECAST_HOURS = FORECAST_DAYS * 24
