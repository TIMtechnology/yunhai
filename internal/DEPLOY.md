# 生产部署备忘（本地专用，不提交 Git）

> 本文件位于 `internal/`，已在 `.gitignore` 中忽略。供 Cursor / 本地开发查阅，避免 SSH 连错机器或漏参数。

---

## 1. 服务器与域名

| 项目 | 值 |
|------|-----|
| **生产域名** | https://yunhai.timkj.com |
| **DNS 解析** | `182.203.168.140` |
| **SSH 用户** | `root` |
| **SSH 端口** | `22`（默认，不是 1021） |
| **SSH 密钥** | `/Users/likun/ssh_2025`（即 `~/ssh_2025`） |
| **部署目录** | `/opt/yunhai` |
| **对外端口** | `8088 → 8080`（nginx 反代到该端口） |

### ⚠️ 常见连错

| 错误写法 | 说明 |
|----------|------|
| `ssh -i ~/ssh_2025 root@182.203.168.140` **不加 IdentitiesOnly** | 可能先试其他密钥，被服务器断开（`Connection closed by remote host`） |
| `182.203.168.153:1021`（`~/.ssh/config` 里的 `jxftech`） | **另一台机器**，跑的是别的项目，**不是 yunhai** |
| 直连 `182.203.168.140:1021` | 端口不通，会 timeout |

---

## 2. SSH 正确用法（复制即用）

```bash
# 推荐：写成变量，所有命令复用
export YUNHAI_SSH="ssh -i /Users/likun/ssh_2025 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
export YUNHAI_SCP="scp -i /Users/likun/ssh_2025 -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
export YUNHAI_HOST="root@182.203.168.140"

# 连通性测试
$YUNHAI_SSH $YUNHAI_HOST 'echo ok && hostname && docker ps --format "{{.Names}}"'
# 期望看到：yunhai-yunhai-1、yunhai-redis-1
```

可选：在 `~/.ssh/config` 增加别名（本地配置，不进 Git）：

```
Host yunhai-prod
  HostName 182.203.168.140
  Port 22
  User root
  IdentityFile /Users/likun/ssh_2025
  IdentitiesOnly yes
```

之后：`ssh yunhai-prod`

---

## 3. Docker 布局（服务器上）

```bash
cd /opt/yunhai
# compose 文件名在服务器上是 docker-compose.prod.yml（由 docker-compose.deploy.yml 上传而来）
docker compose -f docker-compose.prod.yml ps
```

| 容器 | 镜像 | 说明 |
|------|------|------|
| `yunhai-yunhai-1` | `yunhai:latest` | FastAPI + 静态前端 |
| `yunhai-redis-1` | `docker.1ms.run/library/redis:7-alpine` | 缓存 |

**持久化卷（勿删）：**

- `cloudsea_data` → `/app/data/cloudsea`（标注库、社区点位）
- `analytics_data` → `/app/data/analytics`

**注意：** 不要把空的 `scenic-spots` 目录挂载进容器，会覆盖镜像内精选 JSON，导致 `/api/spots/*` 全 404。

---

## 4. 完整发布流程（推荐）

在**本机 Mac**（项目根目录）：

```bash
# 1. 构建 amd64 镜像（需 frontend/.env.local 里有 VITE_TIANDITU_KEY）
./scripts/build-amd64.sh
# 产出：yunhai-amd64.tar

# 2. 上传镜像 + compose
$YUNHAI_SCP yunhai-amd64.tar docker-compose.deploy.yml $YUNHAI_HOST:/opt/yunhai/

# 3. 服务器加载并重启
$YUNHAI_SSH $YUNHAI_HOST 'set -e
cd /opt/yunhai
cp -f docker-compose.deploy.yml docker-compose.prod.yml   # 保持服务器文件名一致
docker load -i yunhai-amd64.tar
docker compose -f docker-compose.prod.yml up -d --force-recreate yunhai
sleep 2
curl -s http://127.0.0.1:8088/health
docker compose -f docker-compose.prod.yml ps
'
```

线上验证：

```bash
curl -s https://yunhai.timkj.com/health
# {"status":"ok"}
```

---

## 5. 后端热补丁（仅改 Python、来不及 rebuild 时）

> 热补丁在容器重启后仍保留，但**下次 `docker load` 新镜像会被覆盖**，记得随后走完整发布。

```bash
# 本机上传
$YUNHAI_SSH $YUNHAI_HOST 'mkdir -p /opt/yunhai/patch'
$YUNHAI_SCP backend/app/services/xxx.py $YUNHAI_HOST:/opt/yunhai/patch/

# 拷入容器并重启
$YUNHAI_SSH $YUNHAI_HOST '
docker cp /opt/yunhai/patch/xxx.py yunhai-yunhai-1:/app/app/services/xxx.py
docker restart yunhai-yunhai-1
sleep 3
curl -s http://127.0.0.1:8088/health
'
```

---

## 6. 查日志 / 排错

```bash
# 最近日志
$YUNHAI_SSH $YUNHAI_HOST 'docker logs yunhai-yunhai-1 --tail 100'

# 实时跟踪
$YUNHAI_SSH $YUNHAI_HOST 'docker logs yunhai-yunhai-1 -f --tail 50'

# 容器内直接打 API（绕过 nginx）
$YUNHAI_SSH $YUNHAI_HOST 'curl -s http://127.0.0.1:8088/health'

# Admin Token 与 compose 里 CLOUDSEA_ADMIN_TOKEN / ANALYTICS_ADMIN_TOKEN 一致
# 见 docker-compose.deploy.yml（该文件在 Git 中，Token 勿写进公开 README）
```

---

## 7. 环境变量要点（`docker-compose.deploy.yml`）

- `TZ=Asia/Shanghai` — 日出窗口计算依赖上海时区
- `CLOUDSEA_ENABLED=true` — 标注 / ML 功能
- `CLOUDSEA_CONTRIBUTE_ENABLED=true` — 社区标注
- `CLOUDSEA_DB_PATH=/app/data/cloudsea/cloudsea.db` — 标注数据持久化
- `REDIS_URL=redis://redis:6379/0`

修改 compose 后：

```bash
$YUNHAI_SCP docker-compose.deploy.yml $YUNHAI_HOST:/opt/yunhai/docker-compose.prod.yml
$YUNHAI_SSH $YUNHAI_HOST 'cd /opt/yunhai && docker compose -f docker-compose.prod.yml up -d'
```

---

## 8. Cursor Agent 操作约束（历史约定）

- 生产服务器：**仅做部署、查日志、验证 API** 等必要操作
- 防火墙 / 安全组 / 装软件等非必要变更需**先征得用户授权**
- SSH 务必带 `-o IdentitiesOnly=yes`，目标 **182.203.168.140:22**，不是 `jxftech`（168.153）

---

## 9. 更新记录

| 日期 | 说明 |
|------|------|
| 2026-05-29 | 初版：SSH IdentitiesOnly、168.140 vs 168.153、完整发布与热补丁流程 |
