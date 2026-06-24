# 生产部署备忘

> 敏感信息（服务器 IP、SSH 密钥路径）请写在 **`scripts/deploy.local.env`**（已 gitignore，勿提交）。  
> 模板：`scripts/deploy.local.env.example`

---

## 1. 本地配置

```bash
cp scripts/deploy.local.env.example scripts/deploy.local.env
# 编辑 deploy.local.env：
#   YUNHAI_SSH_KEY=/path/to/private_key
#   YUNHAI_HOST=root@your.server
```

可选变量：`YUNHAI_REMOTE_DIR`、`YUNHAI_CONTAINER`（见 example 文件）。

---

## 2. SSH 正确用法

```bash
source scripts/deploy.local.env
SSH="ssh -i $YUNHAI_SSH_KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"

# 连通性测试
$SSH "$YUNHAI_HOST" 'echo ok && hostname && docker ps --format "{{.Names}}"'
# 期望看到：yunhai-yunhai-1、yunhai-redis-1
```

### 常见连错

| 错误写法 | 说明 |
|----------|------|
| SSH **不加** `-o IdentitiesOnly=yes` | 可能先试其他密钥，被服务器断开 |
| 连到 `~/.ssh/config` 里别的 Host | 可能是另一台机器，不是 yunhai |

可选：在 `~/.ssh/config` 增加别名（本地配置，不进 Git），`HostName` / `IdentityFile` 填真实值。

---

## 3. 热补丁（推荐日常发版）

```bash
SKIP_TRAIN=1 bash scripts/hot-patch-prod.sh
```

仅 `docker cp` + restart，不覆盖 compose / env。

---

## 4. 全量镜像发布

```bash
./scripts/build-amd64.sh
bash scripts/deploy-prod.sh yunhai-amd64.tar
```

---

## 5. 同步生产 DB 到本地

```bash
bash scripts/sync_cloudsea_db_from_prod.sh
```

---

## 6. 生产域名

对外：https://yunhai.timkj.com（DNS 指向生产机，具体 IP 见 `deploy.local.env`）

默认容器：`yunhai-yunhai-1` · 宿主机健康检查：`http://127.0.0.1:8088/health`
