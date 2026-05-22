# 内部统计分析（不公开、不 push 到 GitHub）

本目录包含本地看板与配置，用于分析 yunhai 线上用户行为。

## 使用

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
