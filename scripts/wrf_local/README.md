# WRF 区域增强第一阶段

本目录用于在本地 Mac 或线上 Linux Docker 环境中验证 WRF 作为“区域气象证据增强”的可行性。第一阶段目标不是替换现有 Open-Meteo + 规则/ML 预测链路，而是先为五女山生成可复现的 9km 单域 WRF 案例，并提取低云、中云、高云、风场、湿度、云底云顶和逆温证据。

## 目录约定

- `docker/wrf/Dockerfile`：Linux WRF/WPS 运行基座，包含编译依赖和 Python 后处理依赖。
- `data/wrf-local/fixed/`：固定资源缓存，例如 WRF/WPS 编译产物、WPS GEOG 数据。
- `data/wrf-local/cache/gfs/<cycle>/`：GFS 初边值 GRIB2 缓存。
- `data/wrf-local/runs/<case>/<cycle>/`：单次 WRF 运行目录。
- `data/wrf-local/products/<case>/`：后处理 JSON、HTML 报告等轻量产物。

`data/wrf-local/` 已加入 `.gitignore`，避免大文件误提交。

## 构建容器

```bash
docker build --platform linux/amd64 \
  -t yunhai-wrf:phase1-amd64 \
  -f docker/wrf/Dockerfile .
```

进入容器：

```bash
docker run --rm -it --platform linux/amd64 \
  -v "$PWD:/repo" \
  -v "$PWD/data/wrf-local:/work" \
  yunhai-wrf:phase1-amd64
```

WRF/WPS 在 Linux 下生成的程序名也以 `.exe` 结尾，例如 `wrf.exe`、`real.exe`、`geogrid.exe`。这是 WRF 项目的历史命名习惯，不是 Windows 可执行文件。线上服务器是 x86 时，本地 Mac 也建议固定 `--platform linux/amd64`，这样 configure 选项与线上一致；Apple Silicon 会通过 Docker 仿真运行，编译较慢但结果更贴近部署环境。

## 编译 WRF/WPS

在容器里执行：

```bash
cd /repo
bash scripts/wrf_local/build_wrf_stack.sh
```

默认编译：

- WRF `v4.5.2`
- WPS `v4.5`
- WRF `em_real`
- 3 个并行编译任务

编译产物缓存到：

```text
data/wrf-local/fixed/
```

如果 configure 选项因架构差异失败，可以通过环境变量重试：

```bash
WRF_CONFIGURE_OPTION=34 WRF_NESTING_OPTION=1 WPS_CONFIGURE_OPTION=3 \
  bash scripts/wrf_local/build_wrf_stack.sh
```

## 下载 GEOG 固定数据

```bash
cd /repo
bash scripts/wrf_local/download_geog.sh
```

默认下载 WPS mandatory geog 数据到：

```text
data/wrf-local/fixed/geog/
```

## 准备五女山案例

默认 dry-run，不下载、不执行 WRF：

```bash
python3 scripts/wrf_local/run_case.py --case wunvshan --cycle 2026060300
```

输出：

- `namelist.wps`
- `namelist.input`
- `manifest.json`
- 预期执行命令清单

实际执行时再加：

```bash
python3 scripts/wrf_local/run_case.py --case wunvshan --cycle 2026060300 --execute
```

下载五女山周边 GFS 子区域数据（默认 dry-run）：

```bash
python3 scripts/wrf_local/download_gfs.py --cycle 2026060300
```

真实下载：

```bash
python3 scripts/wrf_local/download_gfs.py --cycle 2026060300 --execute
```

GFS GRIB2 文件会缓存到：

```text
data/wrf-local/cache/gfs/<cycle>/
```

准备并真实运行五女山 WRF：

```bash
python3 scripts/wrf_local/run_case.py \
  --case wunvshan \
  --cycle 2026060300 \
  --download-gfs \
  --execute \
  --work-root /work
```

## 后处理 WRF 输出

WRF 跑完后，将 `wrfout_d01_*` 转成云海证据 JSON：

```bash
python3 scripts/wrf_local/postprocess_wrf.py \
  data/wrf-local/runs/wunvshan/2026060300/wrfout_d01_2026-06-03_00:00:00 \
  --case wunvshan \
  --lat 41.31976 \
  --lon 125.40773 \
  --output data/wrf-local/products/wunvshan/2026060300.cloudsea-evidence.json
```

生成本地 HTML 对比报告：

```bash
python3 scripts/wrf_local/build_compare_report.py \
  --wrf-evidence data/wrf-local/products/wunvshan/2026060300.cloudsea-evidence.json \
  --output data/cloudsea/reports/wrf-openmeteo-wunvshan-2026060300.html
```

或生成五女山 Open-Meteo + ML + WRF 综合对比报告：

```bash
PYTHONPATH=backend python3 scripts/wrf_local/build_report.py
```

## 磁盘与缓存治理

默认只预览清理计划：

```bash
python3 scripts/wrf_local/cleanup_wrf_cache.py
```

真正删除：

```bash
python3 scripts/wrf_local/cleanup_wrf_cache.py --execute
```

默认策略：

- GFS 缓存保留 3 天。
- WRF 运行目录保留 7 天。
- 轻量报告和证据产物保留 30 天。
- 每次执行前检查 `data/wrf-local/` 至少有 12 GiB 可用空间。

## 资源控制建议

在 3C4G 可用资源下，第一阶段只跑五女山 9km 单域、48 小时预报，并使用 3 个 MPI 进程：

```bash
mpirun -np 3 wrf.exe
```

东灵山暂作为第二案例，五女山跑通并确认耗时、内存、磁盘峰值后再串行增加。
