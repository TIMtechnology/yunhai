# 内部文档（不公开、不 push 到 GitHub）

本目录包含本地看板、标注说明与论文草稿，用于 yunhai 研发与投稿准备。

## 文档索引

| 文件 | 说明 |
|------|------|
| [`CLOUDSEA-LABEL.md`](CLOUDSEA-LABEL.md) | 云海标注工具使用、ML 训练说明 |
| `analytics-dashboard.html` | 用户行为分析看板 |

## 使用（分析看板）

1. 复制配置：
   ```bash
   cp config.example.json config.local.json
   # 编辑 admin_token，与服务器 ANALYTICS_ADMIN_TOKEN 一致
   ```

2. 启动本地静态服务（不能直接双击 HTML 打开）：
   ```bash
   ./serve-dashboard.sh
   ```

3. 浏览器打开：http://127.0.0.1:8765/analytics-dashboard.html

## 注意

- `config.local.json` 已加入 .gitignore
- 此功能仅在 `internal/analytics` 分支，勿合并到公开 main 或 push GitHub
