#!/usr/bin/env bash
# 生产冒烟：验证健康、ML 融合预测、安全响应头和匿名会话 Cookie
set -euo pipefail
cd "$(dirname "$0")/.."

BASE="${SMOKE_BASE_URL:-https://yunhai.timkj.com}"

echo "== 1. /health =="
curl -sf "$BASE/health" | python3 -c "import json,sys; d=json.load(sys.stdin); assert d.get('status')=='ok', d; print('ok')"

echo "== 2. 五女山 ML + 规则融合预测 =="
curl -sf "$BASE/api/predict/wunvshan/viewpoint/dianjiangtai?hours=48" | python3 -c "
import json,os,sys
d=json.load(sys.stdin)
ml=d.get('location',{}).get('ml_status',{})
print('ml_active', ml.get('ml_active'), 'mode', ml.get('mode'))
expected=os.environ.get('SMOKE_EXPECT_ML_ACTIVE','true').lower() in ('1','true','yes')
assert bool(ml.get('ml_active')) is expected, ml
if expected:
    assert ml.get('mode') == 'wunvshan_model', ml
    assert ml.get('has_spot_model'), ml
days=d.get('days') or []
assert days, 'no days'
print('predict ok, days', len(days))
"

echo "== 3. 安全响应头 =="
curl -sS -D - -o /dev/null "$BASE/health" | python3 -c "
import sys
h=sys.stdin.read().lower()
assert 'strict-transport-security:' in h, h
assert 'content-security-policy:' in h, h
assert 'x-content-type-options: nosniff' in h, h
print('security headers ok')
"

echo "== 4. 匿名贡献会话 Cookie =="
curl -sS -D - -o /dev/null -X POST "$BASE/api/contribute/session" | python3 -c "
import sys
h=sys.stdin.read().lower()
cookie=next((line for line in h.splitlines() if line.startswith('set-cookie:')), '')
assert 'httponly' in cookie and 'secure' in cookie and 'samesite=lax' in cookie, cookie
print('contributor cookie ok')
"

echo "== 5. 社区精选 cs_* =="
curl -sf "$BASE/api/spots/search?q=&curated_only=true" | python3 -c "
import json,sys
d=json.load(sys.stdin)
cs=[x for x in d['results'] if x['id'].startswith('cs_')]
print('cs_ spots', len(cs))
"

echo ""
echo "冒烟测试通过。"
