# 使用与接口约定

## 脚本

Python 标准输入约定通过 `ops_sdk.inputs()` 读取 JSON，`ops_sdk.result(value)` 输出结构化结果。`print(..., flush=True)` 用于实时日志。

```python
from ops_sdk import inputs, result
params = inputs()
print('开始检查', flush=True)
result({'healthy': True, 'service': params.get('service', 'demo')})
```

Shell 的参数存放在 `OPS_INPUT_JSON`；输出纯 JSON 时会自动识别为结构化结果。混合 stdout 输出会作为文本保留，可通过下游解析节点提取。Shell 若需要既记录日志又输出独立结构化结果，可写入 `$OPS_RESULT_FILE`：

```sh
#!/bin/sh
set -eu
printf '开始执行\n'
printf '{"healthy":true}\n' > "$OPS_RESULT_FILE"
```

参数定义包含 `name`、`type`、`required`、`default`、`secret`；支持 string、integer、number、boolean、json。默认变量与运行参数合并，运行参数优先。参数总 JSON 长度不超过 64 KiB，资源内容不超过 1 MiB，结构化结果不超过 1 MiB。脚本超时 1–86400 秒，默认 300 秒。

点击脚本或应用的“测试运行”时，运行参数 JSON 自动加载当前账号在同一资源中最近一次顶层运行的参数（包括测试和正式运行），加载后仍可修改。首次运行显示 `{}`；子任务、其他资源及其他账号的参数不参与回填，不受运行列表条数限制。历史或当前定义中声明为 `secret` 的参数不回显，弹窗会列出需要重新填写的参数名称；未保存的草稿秘密参数定义也会生效。

secret 参数在运行详情、日志和返回结果中进行字符串脱敏；不要把密码硬编码在脚本或默认变量中，默认变量与平台数据库本身不是密钥保险库。同 app 账号执行模式下，不能对同账号脚本提供密钥隔离。

若脚本声明另一个脚本作为依赖，发布后可调用：

```python
from ops_sdk import run_script, result
value = run_script('已声明的脚本ID', {'service': 'demo'})
result(value)
```

Shell 调用使用指定 Python 解释器：

```sh
/data/app/anaconda3/bin/python3 -m ops_sdk call '已声明的脚本ID' '{"service":"demo"}'
```

SDK 路径由执行器通过 PYTHONPATH 注入。内部令牌只能调用已声明、已锁定版本、当前用户仍有权访问的依赖，不能当作普通用户 JWT 调用管理 API。

## 应用和解析

应用画布通过连线建立依赖。根节点读取 `$.inputs`，后续节点读取 `$.nodes.节点ID.字段`。脚本节点的参数映射可以是常量，也可以是 `{ "path": "$.nodes.check.service" }`。

选中节点或连线后，可以点击工具栏“删除选中项”、侧栏“删除节点 / 删除连线”，或按 Delete / Backspace 删除；删除节点会一起移除它的关联连线。删除最后一个节点后仍可保存空流程草稿，添加有效节点后才能发布或测试运行。

节点名称、参数映射、解析规则、条件期望值和连线条件均通过顶部“保存”一次保存，不再需要额外点击应用规则。切换节点或“参数与配置”标签页会保留尚未保存的节点配置；JSON 格式错误会指出节点和字段并阻止保存、发布及测试，修正后再保存。

解析器支持 JSONPath、正则命名捕获、CSV 表头行和字符串分隔。字段映射可设置输出名称、源路径、类型、默认值、必填。正则调用有 0.2 秒匹配超时，解析输入限制 1 MiB。

条件节点使用 eq / ne / exists / contains / gt / lt；出线可指定 true 或 false。节点等待所有前驱结束；失败前驱会使下游跳过；条件未选中的分支跳过；合流节点在没有失败前驱且至少一条有效成功入边时运行。应用有任意失败、取消或中断节点时标记失败。互不依赖的脚本节点并行排队运行。

运行记录和最近运行仅展示顶层任务；应用派生的脚本与嵌套调用通过父任务详情逐层下钻查看。

运行记录页支持按任务类型（全部 / 脚本任务 / 应用任务）、任务名称关键词、状态、触发方式（手动 / 测试 / 定时）和开始时间范围组合筛选。设置条件后点击“查询”或按回车；“重置”恢复全部条件。每页最多 100 条，可翻页查看；刷新和下钻返回保留已应用的条件。名称按普通文本模糊匹配，`%` 和 `_` 不作为通配符。

开始时间范围按北京时间填写，结束值包含所选分钟，可只填写起点或终点；未实际开始的任务不匹配时间范围，表格开始时间显示 `—`。筛选在后端返回条数限制之前执行，且不改变用户可访问的记录范围。

应用异常查看：运行记录 → 应用运行 → 点击节点或子任务。脚本节点展示输入参数、输出结果和实时日志，并可返回父任务；解析和条件节点展示实际输入、输出与节点执行日志，失败节点包含错误堆栈。跳过或尚未执行的节点会提示没有执行输入输出。秘密参数仍按声明脱敏。

节点明细从更新后的新运行开始保存；历史运行保留已有子任务和结果，对未保存的节点明细明确提示缺失，可参考应用日志。节点颜色：绿色成功、红色失败、蓝色运行中；文字标签同时显示状态。

应用的“输出详情”包含 `outputs`（按节点 ID 保存的各节点输出）和 `final_result`（最终结果）。最终结果取流程中没有出线、且执行成功的终点节点：只有一个时直接返回其输出，多个时按节点 ID 汇总，跳过的分支不参与。应用未完成、失败、取消、中断，或没有可用的成功终点时，`final_result` 为 `null`；`false`、`0` 和空集合等合法输出会原样保留。例如：

```json
{
  "outputs": {"check": {"healthy": true}, "parse": {"ok": true}},
  "final_result": {"ok": true}
}
```

API 在应用运行的 `result.final_result` 中返回同一结果，原有 `result.outputs` 和 `result.value` 保持兼容。历史运行按执行时的流程快照及已保存的节点状态、输出计算此字段，不修改原始记录。

运行不自动重试，避免重复发送消息、重复部署等副作用。执行器正常停止会取消子进程并标记中断；异常重启会把未完成记录标记中断，并根据 PID 启动指纹清理可识别的遗留脚本进程，不自动重放。

## 版本与市场

编辑改变草稿，正式执行使用最新已发布版本；一次执行锁定全部依赖快照，不受后续编辑影响。定时计划每次触发读取当时最新发布版本。脚本库和应用编排的资源卡片及编辑页均提供删除入口；删除采用保留历史的软删除，会从资源列表移除资源并停用直接关联计划，但保留已发布版本、历史运行和审计记录。后续使用不可访问依赖会明确报错。

管理员创建平台共享资源；开发者仅能编辑自己的私有资源。管理员有平台管理权限。市场提交的是已发布快照，审批后其他开发者可下载。私有依赖随应用复制为个人资源，平台依赖保留锁定版本引用；不携带运行数据、默认变量和 secret 参数默认值。审核时仍需检查代码中是否硬编码敏感信息。

市场更新不自动覆盖已有副本。再次下载新条目会产生新的独立副本，用户可通过版本比较和编辑迁移需要的变化。

## AI

管理员在系统设置中配置 Base URL、协议和密钥。填写服务地址和 API Key 后，平台优先调用 `/v1/models` 获取可用模型；若 Anthropic 兼容地址以 `/anthropic` 结尾且上游返回 404/405，则回退到服务根路径的 `/models`。管理员通过下拉框选择模型。Anthropic 使用 `/v1/messages`、`x-api-key` 和可配置 `anthropic-version`；OpenAI 兼容模式使用 `/v1/chat/completions`。Base URL 可含 `/v1`，平台会规范化路径。

AI 默认获得有权查看的资源名称、标签、参数和版本目录（最多 200 项）；选定参考资源后附上代码 / 流程定义，不隐式附带日志、运行参数或默认变量。输出建议、风险说明和可编辑草稿；不会自动执行或发布。没有模型配置时返回明确错误。接入使用服务器 TLS 证书校验；内网 CA 可通过 `.env` 中的 `OPS_AI_CA_BUNDLE=/证书路径/internal-ca.pem` 配置，不要禁用 TLS 验证。

管理员可在“AI 初始化”页面输入现有脚本或目录的绝对路径：

- **基础工具**：读取 Python / Perl / Shell 源脚本，标准化为可编辑的 Python / Shell 平台脚本草稿。
- **监控编排**：分析监控场景，仅复用真实已发布的平台基础工具，生成同时包含脚本、数据解析和条件分支节点的应用草稿。因此应先完成基础工具初始化、审核和发布。

每个成功条目强制带有 `AI初始化` 及类型标签，同时保留 AI 识别的场景标签、源文件路径、SHA-256、原始语言和模型名称。同一源路径不会重复初始化。批次内单条失败不会回滚其他成功草稿；所有草稿均需管理员检查、测试和手动发布。脚本库与应用编排页支持按名称、描述或标签搜索；API 另支持 `q` 模糊搜索和 `tag` 精确标签过滤。

## 开放 API

服务运行后查看 `/docs` 和 `/openapi.json`。

- `POST /api/auth/login`：username / password → 有效期一小时的访问 JWT。
- `Authorization: Bearer <JWT>`：访问资源、运行、计划、市场、审计等 API。
- `POST /api/auth/password`：old_password / new_password；更改后撤销原登录。
- `POST /api/settings/ai/models`：管理员按 Base URL、协议和密钥从模型服务获取可选模型。
- `POST /api/resources`、`PUT /api/resources/{id}`：创建、保存草稿；携带 updated_at 可检查编辑冲突。
- `POST /api/resources/{id}/publish`：发布新版本。
- `POST /api/resources/{id}/run`：`{"inputs":{},"test":false}`；返回 run_id，后台排队执行。
- `GET /api/resources/{id}/last-inputs`：当前账号最近一次顶层运行的 `run_id`、可回填 `inputs` 和需重填的 `omitted_secrets`；无历史时返回空参数。
- `GET /api/runs?limit=100&offset=0`：仅列出顶层任务，支持 `kind`、`name`、`status`、`trigger`、`started_from` 和 `started_to` 组合筛选，先过滤再分页。时间参数使用 ISO 8601；有时区时转换为 UTC，无时区时按北京时间解释；`started_from` 含边界，`started_to` 不含边界。
- `GET /api/runs/{id}`：状态、输入、结果、直属子任务及应用全部节点明细；节点的 `run_id` 可用于继续下钻。
- `GET /api/runs/{id}/logs?offset=0`：运行的增量日志。
- `POST /api/runs/{id}/cancel`：请求取消及级联取消子任务。
- `POST /api/schedules`：resource_id、五段 cron、timezone、inputs。
- `POST /api/parsers/preview`：input、parser → 解析预览。
- `POST /api/ai/assist`：prompt、mode、resource_id、skip_reuse → 建议 / 草稿。
- `POST /api/ai/initialize/scan`（仅管理员）：paths → 可初始化文件、语言、大小与跳过原因。
- `POST /api/ai/initialize`（仅管理员）：paths、type(`tool` / `monitor`)、instructions → 已创建草稿与单条失败列表。
- `GET /api/resources?q=关键词&tag=标签`：按名称 / 描述 / 标签模糊搜索，并可用逗号分隔多个精确标签。

WebSocket 地址：`ws(s)://服务器/ws/runs/<run_id>`。连接后 10 秒内发送首条消息认证，不把 JWT 放进 URL：

```json
{"token":"JWT","offset":0}
```

服务推送 `{offset, events:[{time,stream,text}], status, truncated}`，断线后携带最近 offset 继续。每轮继续验证账号与 JWT；停用或过期后关闭连接。任务终态且日志读取完毕后服务端关闭。HTTP 日志 API 可作为外部客户端的轮询替代。

账号停用与密码重置会改变 token_version，已有 JWT 不再有效。管理员不能直接停用或降级当前登录账号。用户不物理删除，以保留审计归属；停用实现账号退役。
