多平台整合：双阶段自动执行与通用CSV工作台

Windows独立试运行：安装Python 3.11及以上，双击 `start.cmd`。无需Node、VS Code或额外Python包；默认仅本机访问，运行数据写入 `runtime/state`。模型连接检查、配置、示例及历史迁移边界见 [中文试运行说明](docs/windows_trial.md)，当前Agent能力见 [能力与边界](docs/agent_capabilities.md)。真实模型未配置时保留中文手动流程。

教师页现支持统一自然语言入口、真实CSV特征驱动的LLM方案、独立考核项目、固定基准的局部调整、试算与采用。明确规则通过程序检查后连续执行；缺项只显示相关问题，已知规则跨阶段保留。未配置模型时明确标注限制，自定义项目与调分可用中文表单完成。详见 [自然语言教师流程](docs/natural_language_pipeline.md)。

最新双阶段入口：[http://127.0.0.1:8765/pipeline](http://127.0.0.1:8765/pipeline)。同一次工作流自动执行 **DWD对齐/归并/折算 → 持久化宽表 → 异常确认卡点 → DWS计算/试算**；没有未决异常时不需要额外点击确认。完整配置、默认策略、输出位置、复杂公式与实际验收见 [双阶段说明](docs/two_stage.md)。

教师页面现支持：一个任务输入框、卡片内逐条身份核对、中文数据整理表单、权重与特殊状态表单、变更摘要确认、刷新与草稿恢复。常见流程无需编辑JSON。操作与本轮验收对照见 [教师交互与验证](docs/pipeline_teacher_ux.md)。

待确认卡片已提供提交时间补充入口、明确的处理模式和重复保存防护；数据待核对时也可先保存新权重，之后自动按保存规则计算。[专项修复与实际验证](outputs/checkpoint_fixed_acceptance.md)。

```powershell
Set-Location 'D:\programs\multi-platform-grade-agent'
python -u -X utf8 app.py --port 8765
# 浏览器打开 http://127.0.0.1:8765/pipeline
```

以下为仍可使用的通用CSV工作台（根路径 `/`）说明。

通用模式在本地网页完成：自然语言任务 → 上传两个文件 → 实际读取检查 → 编辑映射/关联/去重方案 → 明确确认 → 执行 → 结果与待确认记录下载。入口为 app.py 的根路径，不依赖固定文件名或业务列名，不导入模拟数据生成器；双阶段业务计算位于独立的 `/pipeline` 工作流。

Python 3.11 及以上，仅使用标准库。网页采用原生 HTML/CSS/JavaScript，无需安装 Web 框架或前端依赖。

启动（PowerShell）：

```powershell
Set-Location 'D:\programs\multi-platform-grade-agent'
python -u -X utf8 app.py --port 8765
```

在浏览器打开 http://127.0.0.1:8765 。Ctrl+C 停止服务。VS Code“终端 → 运行任务 → 交互整合：启动本地界面”也可启动。旧版用户自定义任务与成绩CLI仍保留，旧版文档原样备份在 docs/legacy_grades.md；它们没有接入新的正式入口。

界面操作：

1. 填写希望整合的对象、业务记录与目标，分别选择两个CSV，点击“上传并检查”。文件名和列名均由用户提供。
2. 对照两侧真实文件名、有效记录数、列名、编码、数据预览、空值与重复统计。空字符串显示为∅，0仍是0。坏行、部分读取及不支持格式会明确报告并阻止执行；原始字节始终保留，可下载。
3. 在字段映射中编辑“统一字段”。默认只沿用原列名，字面不同的字段不会自动视为同义。“统计候选对应”展示真实原值交集，可填入草稿；它不代表语义模型判断。不同语义的字段应使用不同统一字段名。
4. 明确每一行代表什么。选择保留独立记录，或选择关联字段/组合键并说明其身份与业务粒度依据。同一对象多次业务记录应加入记录区分字段。当前支持最多3列组合键；仅姓名/名称的常见标签会被阻止，未知语义仍须用户核对。
5. 选择全部保留，或明确说明整行一致为何是冗余副本，再选择文件内完全重复去重。格式转换仅支持保持原值、去首尾空格、大小写归一；后两者执行前需独立确认，原值留在处理明细。
6. 点击“保存方案并检查”，查看候选键缺失与重复、实际冲突记录、拟删除/拟关联/需保留清单和数量摘要。勾选相关确认项后执行。确认文字不能跳过字段存在性、映射完整性、键完整性与基数检查。
7. 查看结果、待确认记录，下载结果CSV、待确认CSV、处理明细JSON。所有输出均含原文件和物理行位置。修改任务或方案会立即停止旧结果展示，服务端旧下载失效；保存、核对、确认后基于同一上传继续，无需重传。刷新页面恢复最近会话，重启服务也能读取磁盘会话。

例如使用 tests/data/01_synonyms 下两份模拟文件：把A客户号/B编号映射为“对象编号”，A联系人/B联络人映射为“姓名”，A联系电话/B手机号映射为“电话”；明确每行一个联系人，选择对象编号键并说明其作用域，再保存、核对与确认。本例中的映射由使用者输入，程序没有硬编码该映射。

执行边界和数量解释：

- 关联采用全外保留：无匹配记录继续保留。缺关联键不会用空值相互匹配。先按明确方案去重，再校验关联键；一对多/多对多不展开、不选择任一条，相关各条原记录独立保留为待确认。
- 同一统一字段有不同非空值时不选边、不合并该组；两侧原值各自保留，并输出双方来源。其余无冲突组正常处理。可修改字段映射以明确分别保留平台观测值，调整组合键，或选择不关联后继续。
- 去重仅检查同一文件全部原字段和原值完全一致；不会因为同对象、同名、同义字段或格式归一后相同而删除。删除副本时仍把副本的完整原始来源附到保留记录中。
- 编号和值均按字符串处理，不补零、不补姓名/日期、不推断电话和日期格式。空值永远不补成0。两侧非冲突字段的已知值可在用户确认关联后汇集，逐字段记录实际来源。
- 输入记录数 − 删除的冗余副本数 − 成功关联的记录对数 ＝ 输出行数。每条输入来源必须被输出覆盖，执行器对此进行断言。待确认部分包含在输出中，也可单独下载；它们没有被排除或伪装成已合并。
- 页面结果预览与CSV导出共用 export_view；表格最多展示前50行，明确标示预览/总行数。下载包含全部结果。处理明细包含完整原值、映射、转换、去重、关联、冲突及会话事件。

读取范围：每份CSV最多2 MiB、5000条记录、128列。优先UTF-8/UTF-8 BOM；严格解码失败后有限尝试GB18030，UTF-16仅按BOM识别。识别逗号、分号或制表符，并显示实际方式。所有解码采用strict，不替换字符；引号/列数错误明确报告。空白物理行显式记录且不计为业务记录；部分读取只用于诊断预览，不能执行或导出整合结果。编码可解码不等于业务文字一定正确，使用者需要核对预览。

状态和输出位置：

| 路径 | 用途 |
| --- | --- |
| state/<会话编号>/A.original、B.original | 用户上传的原始字节，固定内部文件名，原文件名留在元数据中 |
| state/<会话编号>/session.json | 已解析原值、读取统计、任务、方案版本、真实模型建议和当前结果 |
| state/<会话编号>/result-v<版本>.json | 执行结果快照，修改后仍保留用于审计，但不能冒充当前有效下载 |
| outputs/interactive_acceptance.md、.json | 真实HTTP验收的逐项预期和实际结果 |
| outputs/browser_acceptance.json | 真实浏览器上传、编辑、下载、恢复与失效检查 |
| outputs/ui-preview.png | 实际浏览器截图 |
| tests/data/ | 独立模拟数据目录，不由正式入口读取 |

state、outputs、work和本机模型配置均在 .gitignore 中。`--state-dir` 可以指定独立状态目录。服务仅绑定127.0.0.1，面向单机单使用者；会话文件明文留在本机，无生产多用户认证或过期清理功能。

模型配置入口：

```powershell
Copy-Item model_config.example.json model_config.local.json
# 编辑本机JSON：enabled=true，填写完整endpoint、model及api_key_env
$env:INTEGRATION_MODEL_API_KEY = '<由你在本机设置真实密钥>'
python -u -X utf8 app.py --port 8765 --model-config model_config.local.json
```

接口按Chat Completions兼容的POST结构发送 model/messages，并读取 choices[0].message.content 内的JSON建议。endpoint为完整请求地址。只接受HTTPS或本机HTTP。api_key_env填写服务端环境变量名，不在JSON、前端或日志写入密钥。项目没有配置可用接口，因此默认明确显示“语义分析尚未启用”；不会使用环境中其他未指定的服务密钥来假装模型可用。

只在用户点击“请求真实模型建议”时发请求。默认发送任务、列名和统计计数；不发送重复值明细，allow_sample_values=true时才额外发送最多3条样例。建议必须通过结构校验（真实源字段、无目标覆盖、真实组合键等），填入草稿后仍需完整程序校验和用户确认。模型不能生成执行授权标记，也不能直接改写数据。请求或校验失败会显示失败，保留手动流程，不生成模拟建议。真实模型连通性和语义质量本轮未验证。

开发与实际验收：

```powershell
# 显式、独立生成模拟数据（会重写tests/data内的同名测试文件）
python -X utf8 tests/generate_fixtures.py

# 只读取已有测试数据，启动临时真实HTTP服务后执行验收
python -X utf8 tests/test_interactive.py

# 先启动app.py，再运行真实浏览器验收；此项需要测试环境有Playwright与Edge/Chromium
node tests/browser_acceptance.cjs
```

tests/EXPECTATIONS.md在测试运行前列明预期。Python验收无额外依赖。浏览器脚本可通过 PLAYWRIGHT_MODULE 指定Playwright模块路径、BROWSER_EXECUTABLE指定浏览器、INTEGRATION_TEST_URL指定已启动服务；这些都是测试依赖，正式程序不依赖它们。当前实际执行结果：14项Python/HTTP检查、9组真实浏览器检查通过，覆盖全部8类最小案例；其中浏览器实际核对了页面原始预览与解析数据、页面结果与用户下载CSV的逐字段一致性。具体值见outputs中的报告。

实现定位：

| 功能 | 文件与入口 |
| --- | --- |
| 上传与HTTP入口 | app.py：create_server内POST /api/sessions，Base64还原真实上传字节 |
| 读取、物理来源与统计 | integration/csv_io.py：read_upload |
| 状态保存、恢复、版本失效 | integration/store.py：SessionStore.create/load/update/invalidate/execute |
| 模型配置、真实请求、建议校验 | integration/models.py：model_status/get_advice/validate_advice |
| 统计候选、问题与拟执行清单 | integration/engine.py：value_candidates/analyze |
| 执行条件 | integration/engine.py：validate_plan/required_confirmations/execute |
| 归一、去重、全外关联与冲突 | integration/engine.py：prepare_units/build_result |
| 导出与预览共同数据 | integration/engine.py：export_view；app.py：GET /api/sessions/<id>/download |
| 页面交互 | web/app.js：renderReading/collectPlan/renderAnalysis/renderResult |

尚未实现或未运行：真实模型语义质量验收、真实平台数据和API接入、生产多用户与认证、大规模性能测试；CSV以外格式、模糊匹配、日期/电话智能转换、跨文件自动去重、任意文档解析、规则检索与成绩计算不在当前交互版执行范围。
