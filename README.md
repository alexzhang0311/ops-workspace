# 运维助手

Python 后台、浏览器 IDE、可视化 DAG、任务调度与 AI 助理，面向内网离线部署。当前版本 1.0.0。

已实现的是可运行的首版。CentOS 7 实机安装、内网模型联调和生产 300 并发容量验收仍需在目标环境执行，不能把本机测试结果当作生产验收。

## 使用入口

本地预览：`http://127.0.0.1:8765`。本机初始化账号在 `var/local-login.txt`，不进入 Git 或生产包。生产没有默认账号或密码。

```sh
# 已安装依赖的工作目录
sh server.sh start
sh server.sh stop
sh server.sh restart
sh server.sh status
sh server.sh doctor
```

全新开发环境：

```sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
sh server.sh build
sh server.sh init-admin
sh server.sh seed          # 可选：添加无外部副作用的示例脚本和流程
sh server.sh start
```

本地未配置数据库时使用 SQLite，便于预览和功能开发。离线安装器创建 PostgreSQL，生产容量验证必须使用 PostgreSQL。

## 功能

- Python / Shell 脚本 IDE，Monaco 编辑器及字体、worker 均随包提供，不访问 CDN；可指定解释器、参数、变量、命令行参数、超时和依赖。
- 草稿保存、手动测试、不可变发布版本；正式执行锁定脚本及依赖快照。
- DAG 画布：脚本、解析、条件节点，串行、并行、条件分支和合流；绿/红/蓝标示成功、失败、运行中。
- JSONPath、正则命名捕获、CSV、分隔符解析，字段映射、类型转换、必填校验和样例预览。
- 异步执行器、并发配额、排队、超时、取消、stdout/stderr、异常堆栈、应用→节点→子脚本日志下钻。
- Python SDK 和 Shell 命令入口调用登记脚本，权限与父子执行记录保持一致。
- 五段 Cron、上海时区默认值、启停与删除，重叠执行跳过，停机漏触发合并为一次。
- 管理员 / 开发者两类账号；创建、重置密码、改角色、停用与重新启用；停用撤销 JWT、暂停计划并请求取消运行。
- 平台资源共享、个人资源私有；市场提交、内容与依赖查看、审批 / 驳回、下载为独立副本。
- 内网 Anthropic Messages / OpenAI 兼容接口，基于可访问资源生成草稿、优化建议、风险评估；明确勾选时才跳过复用。
- 管理员 AI 批量初始化：从现有 Python / Perl / Shell 脚本生成带场景标签的平台工具草稿，或复用已发布基础工具生成包含解析和条件分支的监控编排草稿。
- JWT REST API、OpenAPI 文档、认证 WebSocket、`/metric` 与 `/metrics`、操作审计及执行统计。

## 生产环境与边界

已知目标：CentOS 7，Linux x86_64，glibc 2.17；Python 3.12.7 位于 `/data/app/anaconda3/bin/python3`，8 核、约 16 GiB；20 位开发者，目标为 300 个 I/O 等待型活动脚本。

按用户最终确认，平台、数据库、脚本统一使用 `app` 账号。**这是开发者互信模型：私有资源由平台权限控制，不提供操作系统级沙箱。** 脚本具有 `app` 的文件和网络访问权限；不能向不可信租户开放执行能力。

全局活动执行配额默认 32、每用户 16；验收后可把全局改为 300。同步等待子调用的父进程临时让出活动配额以防死锁，因此实际存在的 OS 进程可能超过活动配额；调用深度最多 4 层。CPU / 大内存脚本应降低并发。高可用、多机器 Agent、容器沙箱、循环和应用嵌套不在首版中。

生产安装目录：`/data/appsystems/ops-workpace/`。

安装文档：[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)

接口及脚本约定：[docs/USAGE.md](docs/USAGE.md)

验证记录：[docs/VALIDATION.md](docs/VALIDATION.md)

## 构建离线包

联网准备只发生在构建机。生产安装流程不执行网络下载，不依赖生产 Node/npm。

```sh
sh server.sh package --prepare
# 已准备 wheelhouse、PostgreSQL 源码、前端 dist 后，可完全离线重新打包：
sh server.sh package --skip-build
```

产物：`dist/ops-studio-1.0.0-linux-x86_64-py312.tar.gz` 和 `.sha256`。

完整构建、校验与发布说明：[docs/RELEASE.md](docs/RELEASE.md)。

默认附带 PostgreSQL 16.16 官方源码及校验值，安装时用本机 GCC + GNU make 离线编译。若已有兼容 CentOS 7 / x86_64 的 PostgreSQL 安装前缀，使用 `--postgres-runtime /绝对路径` 打入二进制运行时，跳过目标机编译。不能打入 macOS 或较高 glibc 要求的 Linux 二进制。

## 测试

```sh
.venv/bin/python -m pip install -r requirements-dev.in
.venv/bin/python -m pytest -q
.venv/bin/python scripts/smoke.py
# 已安装 PostgreSQL 工具的开发机；创建临时集群，不触碰已有数据库
OPS_TEST_PG_BIN=/path/to/postgres/bin .venv/bin/python scripts/postgres_smoke.py
```
