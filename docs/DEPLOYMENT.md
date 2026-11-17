# 离线部署与运维

## 1. 目标与安装前检查

使用普通 `app` 账号，应用固定安装在 `/data/appsystems/ops-workpace/`（目录名按实际约定拼写）。该目录及用于暂存发布包的 `/data/appsystems/releases/` 必须由 `app` 账号可写；若父目录不可写，由管理员预先创建并授权。生产不需要容器、root、Node、npm 或访问包仓库。

已确认 Python 3.12.7 / x86_64 / glibc 2.17 / OpenSSL 3.0.15。尚需检查编译工具和基础 Python 模块：

```sh
command -v gcc make
make --version
gcc --version
/data/app/anaconda3/bin/python3 -c 'import venv,ensurepip,ssl,sqlite3; print("Python modules OK")'
```

默认包通过源码在目标机器编译 PostgreSQL 16.16。需要 GCC 的 C99 支持、GNU make >= 3.81 和系统开发头文件。关闭 Readline、zlib、ICU、SSL 可选依赖以减少旧系统依赖；数据库仅绑定本机回环地址，使用 SCRAM 认证。该编译方式不提供数据库 TLS 或压缩 pg_dump，备份使用纯 SQL。

如果目标机器缺少编译工具，构建机必须先提供与 CentOS 7 x86_64 兼容的完整 PostgreSQL 安装前缀，再用 `--postgres-runtime` 打包。不要在线 yum 安装，也不要强行替换系统 glibc。

PostgreSQL 16 的源码构建要求和配置选项见[官方构建要求](https://www.postgresql.org/docs/16/install-requirements.html)。16 系列发行源码包含生成文件，避免 17 系列要求的额外 Flex / Bison 构建依赖。

## 2. 首次安装

把 `ops-studio-1.0.0-linux-x86_64-py312.tar.gz` 和同名 `.sha256` 一起传入 `/data/appsystems/releases/`。以下命令仅用于首次安装，目标目录必须为空；已有实例请先阅读第 6 节。校验后剥离压缩包顶层目录，直接解压到最终安装路径；创建虚拟环境后不要再移动目录。

```sh
set -eu
cd /data/appsystems/releases
sha256sum -c ops-studio-1.0.0-linux-x86_64-py312.tar.gz.sha256
mkdir -p /data/appsystems/ops-workpace
if [ -n "$(ls -A /data/appsystems/ops-workpace)" ]; then
  echo "目标目录非空，请按升级流程操作，禁止覆盖已有实例。"
  exit 1
fi
tar -xzf ops-studio-1.0.0-linux-x86_64-py312.tar.gz --strip-components=1 -C /data/appsystems/ops-workpace
cd /data/appsystems/ops-workpace
sh scripts/install-offline.sh
sh server.sh init-admin --username admin
sh server.sh seed     # 可选演示数据
sh server.sh start
sh server.sh status
```

`install-offline.sh` 顺序执行：文件 SHA-256 校验 → 平台检查 → 创建 `.venv` → 从 wheelhouse 安装锁定依赖 → 离线编译 PostgreSQL（有二进制时跳过）→ 初始化数据库 → 创建数据表。所有 pip 操作使用 `--no-index`、`--require-hashes`。安装器检测到 `.env` 会拒绝覆盖，防止破坏已有实例。

应用安装目录的调整不改变基础 Python 路径，仍使用 `/data/app/anaconda3/bin/python3`。管理台虚拟环境创建在 `/data/appsystems/ops-workpace/.venv/`。

自定义 Python：

```sh
OPS_BASE_PYTHON=/你的/python3.12 sh scripts/install-offline.sh
```

管理台默认绑定 `127.0.0.1:8765`，PostgreSQL 默认 `127.0.0.1:55432`。先通过 SSH 端口转发或既有内网 HTTPS 反向代理访问。若需直接监听内网网卡，修改 `.env` 的 `OPS_HOST` 为服务器内网地址，再重启；JWT 登录应通过 HTTPS 接入。反向代理需要允许 WebSocket Upgrade，并把日志连接的读取超时调长。

生产 `.env` 自动生成数据库随机密码，权限 600。JWT 和指标密钥保存在 `var/.jwt-key`、`var/.metrics-key`。AI 密钥保存在 `var/.ai-key`。备份这些文件才能保持密钥一致，不要把它们放入市场或发送到 AI。

## 3. 起停与配置

```sh
cd /data/appsystems/ops-workpace
sh server.sh start
sh server.sh stop
sh server.sh restart
sh server.sh status
sh server.sh doctor
```

启动顺序：PostgreSQL → Web/API → 执行器。停止顺序：执行器 → Web/API → PostgreSQL。执行器持有文件锁，拒绝同数据目录启动第二个实例。API 使用一个进程；异步执行器承担脚本并发，不能把 Uvicorn workers 数量当作脚本容量配置。

下述相对路径均基于 `/data/appsystems/ops-workpace/`。配置为 `.env`，数据库程序为 `vendor/postgres/`，数据库数据为 `var/postgres/`，备份为 `var/backups/`。

Web 启动日志：`var/api.log`；执行器进程日志：`var/worker.log`；数据库日志：`var/postgres.log`；单次任务日志：`var/logs/<run_id>.jsonl`；任务目录：`var/runs/<run_id>/`。

可配置项：

| 配置 | 初始值 | 用途 |
| --- | --- | --- |
| OPS_MAX_CONCURRENCY | 32 | 全局活动脚本配额 |
| OPS_PER_USER_CONCURRENCY | 16 | 单用户活动脚本配额 |
| OPS_LOG_LIMIT_BYTES | 10485760 | 每次运行日志文件约 10 MiB 上限 |
| OPS_BASE_URL | http://127.0.0.1:8765 | 脚本 SDK 内部调用地址 |
| OPS_DATA_DIR | 安装目录/var | 数据和日志根目录；建议固定绝对路径 |
| OPS_DATABASE_URL | 安装器生成 | SQLAlchemy PostgreSQL URL |
| OPS_METRICS_TOKEN | 自动密钥文件 | 可选覆盖指标认证令牌 |
| OPS_AI_CA_BUNDLE | 空 | 内网 AI 服务的 CA PEM 路径，保留 TLS 校验 |
| OPS_SCRIPT_IMPORT_ROOTS | 空 | AI 初始化允许读取的绝对目录，多个目录以 `:` 分隔；空值表示 app 账号可读取的任意绝对路径 |

解释器绝对路径在脚本配置中设置。业务 Python 依赖需由运维离线安装在所选解释器的环境中；管理台不会擅自在线 pip 安装脚本的第三方依赖。

AI 初始化按统一 `app` 账号的实际文件权限读取源脚本。建议生产环境显式配置 `OPS_SCRIPT_IMPORT_ROOTS`，避免将无关目录暴露给批量扫描入口。单文件最大 512 KiB，单批最多 20 个；源文本会发送到所配置的内网 AI 服务。

## 4. 容量验证与 300 并发

8 核 / 16 GiB 不足以无条件承诺任意 300 个脚本。初始 32 配额用于试运行，按 32 → 64 → 128 → 300 分阶段使用业务同类 I/O 脚本验证；每用户 16 配额允许 20 用户合计达到 300 活动执行。

可先执行仓库提供的 `scripts/load_test.py`。它只运行显式创建的测试脚本，但会真实消耗进程、内存和数据库资源，测试前保留业务余量。测试报告记录峰值运行数、成功 / 失败 / 排队数、总耗时；并发配置不足时会报告实际峰值，不会宣称达到 300。

```sh
# 在 .env 调整配额，重启；不要直接在正在繁忙的实例上压测。
sh server.sh restart
.venv/bin/python scripts/load_test.py --base-url http://127.0.0.1:8765 --jobs 300 --hold-seconds 30
```

认证通过交互输入密码。该脚本使用单个登录账号；验证 300 峰值时还需在测试环境临时将单用户配额调至 300，测试后恢复 16。默认模拟等待型负载；这只能验证平台调度规模，不替代真实业务接口限流、内存、文件描述符和网络条件验收。监控 CPU、RSS、可用内存、数据库连接数、响应延迟、错误率和队列积压。无交换空间，应保留内存余量。

同步子脚本调用时，父进程等待并让出活动配额，避免所有槽位被父任务占满导致死锁。最大调用深度 4 层；实际等待进程数可能高于活动配额。批量深层嵌套需单独评估内存。

## 5. Prometheus

使用已有 Prometheus，安装包不另行部署监控平台。

```yaml
scrape_configs:
  - job_name: ops-studio
    metrics_path: /metrics
    scrape_interval: 15s
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/secrets/ops-studio-token
    static_configs:
      - targets: ['管理台内网地址:8765']
```

把 `var/.metrics-key` 的内容通过受控方式放入 Prometheus 的 credentials_file；令牌不是用户 JWT。`/metric` 与 `/metrics` 等价。指标按状态聚合，避免用户名、脚本名和 run_id 造成高基数。生产可在现有代理上将指标路径仅开放给 Prometheus。

## 6. 备份、升级与恢复

首次版本没有历史 schema 迁移需求，数据表初始化使用 SQLAlchemy。后续若改变 schema，必须提供并验证显式迁移，不能只覆盖源文件。

升级前暂停计划并等待运行结束。保存 PostgreSQL 纯 SQL 备份，以及 `.env`、`var/` 中密钥、脚本运行目录和日志。数据库不应通过运行时直接复制 PGDATA 目录来备份。

```sh
# 备份工具读取 .env，密码通过子进程环境传递，不出现在命令行。
sh server.sh backup
```

`backup` 生成 `var/backups/<时间>/database.sql` 和 `application-state.tar.gz`。日志 / 运行目录数量较大时应安排维护窗口；脚本停写后备份一致性最好。备份包含敏感配置，只允许 app 访问，并定期转存到备份存储。

生产最终路径固定为 `/data/appsystems/ops-workpace/`。升级包先解压到独立暂存目录并校验；完成备份、停止旧实例并验证数据库迁移方案后，在维护窗口保留旧实例副本，再部署到最终路径并重建虚拟环境。保留原 `.env`、密钥和 PostgreSQL 数据目录，逐项核对其中的绝对路径；不能直接对已有实例执行首次安装命令，也不能把暂存目录中的 venv 搬到最终路径。

1.0.0 未提供从开发版数据库自动升级的迁移脚本；已有数据的部署必须先在备份副本上验证迁移与恢复，不能仅靠 `db-init` 更新已有表结构。回滚需同时恢复匹配的代码、数据和配置，不能只切换 Python 文件。首版没有自动跨版本迁移 / 回滚按钮。

磁盘清理与审计保留期由部署方制定；首版不会自动删除审计记录和运行历史，避免未经确认清除数据。单运行日志有上限，但历史累计仍需纳入磁盘监控。
