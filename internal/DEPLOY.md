# 生产部署备忘

> 敏感信息（服务器 IP、SSH 密钥路径）请写在 **`scripts/deploy.local.env`**（已 gitignore）。

---

## 标准发版（推荐）

每次版本发布应走 **镜像构建 + compose 重启**，镜像内已包含：

- 前端静态资源（`npm run build`）
- 后端完整代码（含 V7.1 规则 / ML / 预测反馈）
- ML 模型 `spot_*.pkl`、社区精选 `curated-spots/*.json`

容器启动时 **`docker-entrypoint.sh`** 会将 baked 资源同步到 `cloudsea_data` 卷（新 BUILD_ID 时强制覆盖模型）。

```bash
cp scripts/deploy.local.env.example scripts/deploy.local.env   # 首次

# 完整发版（可选训练 → 构建镜像 → 上传 → 重启 → 冒烟）
bash scripts/release-prod.sh

# 已有模型、跳过训练
SKIP_TRAIN=1 bash scripts/release-prod.sh
```

分步：

```bash
./scripts/build-amd64.sh
bash scripts/deploy-prod.sh yunhai-amd64.tar
bash scripts/smoke-prod.sh
```

**发版保证**：`deploy-prod.sh` **只**上传镜像 tar、`docker load`、`compose up -d yunhai`；**不会**上传或修改服务器上的 `docker-compose.prod.yml`、`.env` 及 `environment:` 中的任何密钥。

**注意**：服务器 `docker-compose.prod.yml` 不应再写 `command: uvicorn ...`（可选，镜像 ENTRYPOINT 已含资源同步 + 启动）。如需调整 compose，请 SSH 手工编辑。

---

## 热补丁（仅应急）

`scripts/hot-patch-prod.sh` 仅用于 **来不及构建镜像** 时的临时修复；`docker compose up --force-recreate` 会 **还原为旧镜像代码**，热补丁会丢失。

---

## 本地配置

见 `scripts/deploy.local.env.example`。

---

## 同步生产 DB

```bash
bash scripts/sync_cloudsea_db_from_prod.sh
```
