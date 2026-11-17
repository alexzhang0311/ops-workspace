# 1.0.0 构建与发布

产品名称：运维助手。Git tag 使用 `1.0.0`，不添加 `v` 前缀。发布包沿用 `ops-studio` 文件名前缀；生产最终安装目录固定为 `/data/appsystems/ops-workpace/`。

## 构建输入与产物

- 前端：`frontend/package-lock.json` 锁定依赖，`npm ci` 安装，TypeScript 检查后由 Vite 生成 `frontend/dist/`。Monaco、字体及 worker 随静态文件交付。
- Python：`requirements.lock` 锁定 30 个生产包；构建机下载 CPython 3.12、Linux x86_64、manylinux2014 / glibc 2.17 兼容 wheel。不会复制构建机的 macOS venv 或原生二进制。
- PostgreSQL：16.16 官方发行源码及官方 SHA-256 文件。生产首次安装时用 GCC、GNU make 离线编译到 `vendor/postgres/`，不需要生产联网。
- 应用：`ops/`、`sdk/`、`scripts/`、`docs/`、前端静态文件、`server.sh`、`.env.example`、`requirements.lock` 和 README。
- 包内生成 `requirements-offline.lock`（每个 wheel 的固定版本及 SHA-256）、`target-dependencies.json`（Linux 目标环境与依赖闭包）、`manifest.json`（版本、源码提交及各文件 SHA-256）。版本从 `frontend/package.json` 读取。
- 不打包 `.env`、密钥、账号、业务数据库、运行日志、开发 venv、node_modules 或 Git 元数据。

输出为 `dist/ops-studio-1.0.0-linux-x86_64-py312.tar.gz` 及同名 `.sha256`，压缩包内顶层目录为 `ops-studio-1.0.0/`。GitHub 自动生成的 Source code 压缩包只含源代码，**生产部署请下载单独上传的上述离线包及校验文件**。

## 从源代码构建

构建机需 Python 3.12、Node/npm、Git。首次在联网构建机准备：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock -r requirements-dev.in
.venv/bin/python -m pytest -q
npm --prefix frontend test
sh server.sh package --prepare
```

`--prepare` 自动执行 `npm ci`、`npm run build`、目标 Linux wheel 下载和 PostgreSQL 源码下载与校验，然后打包。目标参数为 `--platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 --python-version 3.12 --implementation cp --abi cp312 --only-binary=:all:`。打包器额外按 Linux 环境重新评估 `Requires-Dist` 条件，避免在 macOS 构建时漏掉 `greenlet` 等 Linux 依赖。

已有对应锁文件的 npm 缓存、`dist/wheelhouse/` 和 `dist/sources/` 时，可复用缓存构建（本次 1.0.0 采用此流程）：

```sh
npm --prefix frontend ci --offline --no-audit --no-fund
npm --prefix frontend test
npm --prefix frontend run build
.venv/bin/python -m pytest -q
# 确认源码已提交且工作区无改动，manifest 记录该提交。
git status --short
sh server.sh package --skip-build
```

仅在本次已重新构建前端后使用 `--skip-build`，否则会把旧静态文件打入包。缓存中的 PostgreSQL 源码在打包时再次校验 SHA-256。更改依赖锁文件后应重新准备依赖缓存。

## 发布前校验

```sh
.venv/bin/python dist/ops-studio-1.0.0/scripts/verify_bundle.py
.venv/bin/python -m pip install --dry-run --ignore-installed --no-index \
  --find-links dist/ops-studio-1.0.0/wheelhouse --require-hashes \
  --platform manylinux2014_x86_64 --platform manylinux_2_17_x86_64 \
  --python-version 3.12 --implementation cp --abi cp312 --only-binary=:all: \
  -r dist/ops-studio-1.0.0/requirements-offline.lock
(cd dist && shasum -a 256 -c ops-studio-1.0.0-linux-x86_64-py312.tar.gz.sha256)
```

另在隔离临时 PostgreSQL 集群执行 `.venv/bin/python scripts/postgres_smoke.py`，验证 API、worker、任务与日志链路。它需要构建机上可运行的 PostgreSQL 工具；开发机二进制不进入生产包。

本机测试、目标依赖解析与哈希校验不等于 CentOS 7 实机安装通过。CentOS 编译和 wheel 导入、内网 AI 联调以及 300 并发仍需生产环境验收，详见 [VALIDATION.md](VALIDATION.md)。

## GitHub 发布

仓库：<https://github.com/alexzhang0311/ops-workspace>。将源码提交推送到仓库默认分支，创建注释标签 `1.0.0` 并推送，然后创建正式 Release，附上离线包和 `.sha256`。发布前确认 `manifest.json` 的 `source_commit` 与标签所指提交一致，使用发布包校验值核对上传附件。已有标签或 Release 不应直接覆盖。

安装步骤见 [DEPLOYMENT.md](DEPLOYMENT.md)。构建产物不提交进 Git 历史，只作为 Release 附件上传。
