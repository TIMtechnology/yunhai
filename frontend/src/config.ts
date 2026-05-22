export const TIANDITU_KEY = import.meta.env.VITE_TIANDITU_KEY ?? ''

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
