双阶段自动执行（DWD → DWS）

正式入口是 app.py，浏览器访问 http://127.0.0.1:8765/pipeline 。每次上传2至6份用户CSV，可以包括主花名册；不依赖测试文件名，正式入口不导入数据生成器。每份最多2 MiB、最多5100条CSV记录、表头最多128列。其他表格格式仍明确不支持。

```powershell
# 在解压后的正式程序目录执行
python -B -u -X utf8 tools/launch.py
```

用户操作：在“你希望如何处理这些数据？”中填写任务，选择各平台文件，点击“整理并计算”。没有未决问题时连续执行两个阶段。页面展示实际理解的归并、分制、权重及状态规则；读取详情可展开核对原值。

身份歧义卡片展示实际文件、物理行、原值及候选人的编号、班级、联系方式，不预选身份。选择编号、填写“核对依据”，直接点击卡片内“确认此条并继续”。选择“继续待确认”会保留记录并继续阻止计算。多条问题逐条处理，其他草稿保留；刷新不要求重传。

“调整数据整理方式”按文件编辑表头、主表、身份字段、统一项目、归并策略、时间字段及分制。先查看变更摘要与影响，再填写依据并确认重新处理。无实际变更不重复生成DWD。权重、特殊状态、小数位、封顶及优秀率使用中文计算规则表单；仅重算DWS。JSON、命令、哈希、路径和诊断默认折叠在高级设置中。详细说明见 [教师交互与验证](pipeline_teacher_ux.md)。

阶段一默认行为与边界：

| 环节 | 已实现的确定性行为 |
| --- | --- |
| 表头探测 | 在前40条CSV记录中探测真实表头；不同强候选冲突时暂停。顶部说明、重复表头、尾部明确的合计/汇总/平均等行保留原值及物理行位置，不计业务记录 |
| 别名 | 按本轮明确授权的别名表将学号/工号/账号/StudentID等映射到student_id，姓名/学生姓名等映射到name；其他字段可通过高级方案或真实模型建议修正 |
| 实验/练习 | 默认max，对实际有效分值选择最高值，记录所有尝试；不会把缺失尝试先补成0 |
| 作业/测验 | 默认latest，只有多条记录时才需要提交时间；不使用文件最后一行代替时间，时间缺失、格式不支持或并列时间不同值时暂停 |
| 考勤 | 有重复时须明确sum或mean；“累加或均值”不是一个唯一策略，系统不会擅自二选一。可在任务中写“考勤累加/考勤均值”，或修改方案 |
| 主表 | 优先自动识别的唯一无成绩花名册，或明确指定roster_file；没有唯一花名册时选择实际不同编号最多的表，并记录选择结果。重复提交行数不影响人数比较 |
| 实体对齐 | 主表Left Join。清除编号、姓名等首尾空格与Unicode不可见字符，保留原值和前导零。无编号只有在姓名＋班级/电话精确一致、辅助字段不矛盾且候选唯一时自动补齐 |
| 模糊候选 | 可以提出姓名相似且辅助字段相符的候选，但不会把概率当作100%确定；同名无辅助信息、多候选或编号姓名矛盾需要教师确认 |
| 分制 | 明确满分的原分折算百分制；默认等级表为优95、良85、中75、及格65、不及格0，支持明确等级覆盖。未知原满分不能凭“换算成百分制”推断。表头与任务满分冲突也需确认 |
| 缺失/特殊 | 空字符串、未交、缺考、缓考、免修、缺勤保留采集状态。缓考和数值并存等不确定尝试组合需要确认；阶段一不把缺失状态填0 |
| 冲突与未匹配 | 同项跨平台冲突、主表外编号等都进入确认清单。草稿显示冲突，不选取一侧。主表外记录不进入计算宽表，但全部留在异常与原始账本；教师可明确排除并给原因，或追加编号后重做阶段一 |

归并不是删除来源。阶段一逐条账本覆盖每条已识别业务记录，折叠尝试、教师身份确认、主表外记录、人工补充值都记录来源和依据；顶部说明、汇总、坏行等另有完整布局审计，原始字节也独立保存。ready表示程序检查通过且没有未决数据治理问题，不代表数学上证明真实世界身份永远正确。

阶段二规则：

简单完整权重可以直接写“实验40%，作业60%，未交记0，缓考保留，保留一位小数”。内置有限语法会转成显式规则JSON，界面显示实际规则。它不是模拟LLM。复杂条件需配置真实LLM提出受限表达式，或直接填写JSON规则。无法理解新指令时会暂停，不会用旧公式冒充变更成功。

```json
{
  "weights": {"实验": "0.4", "作业": "0.6"},
  "missing": {
    "未交": {"action": "zero"},
    "缓考": {"action": "preserve"},
    "免修": {"action": "value", "value": "85"}
  },
  "precision": 1,
  "summary_field": "总评",
  "excellent_threshold": "90",
  "excellent_denominator": "numeric"
}
```

上面的免修85只是明确规则配置示例，不是系统默认。未给出的状态默认hold；空字符串不自动等于未交。missing_by_field可以对单项覆盖，例如 `{"作业":{"":{"action":"zero"}}}`，只有教师明确将该项空缺记0时才这样填写。weights合计必须为1，禁止自行归一。优秀率必须给阈值、摘要字段和分母：numeric只计数值总评，all计全部编号；没有给定义时不计算优秀率。

阶段二宽表保留阶段一采集列，并新增“项目（计算输入）”列显示实际政策后的输入。例如原作业列仍是“未交”，教师明确记0后，“作业（计算输入）”为0，后续公式采用这个实际数值；变化和依据同时留在state_changes审计中。缓考保留政策则让计算输入和总评继续显示缓考。

weights和formulas二选一。复杂规则按列表顺序计算，后序表达式可引用前序输出：

```json
{
  "formulas": [
    {"name":"精选实验","expr":"mean(best(2,field('实验1'),field('实验2'),field('实验3')))"},
    {"name":"封顶平时","expr":"min(100,field('精选实验')+10)"},
    {"name":"总评","expr":"field('封顶平时')*0.4+field('作业')*0.6"},
    {"name":"绩点","expr":"ifelse(field('总评')>=90,4,ifelse(field('总评')>=80,3,0))"}
  ],
  "precision": 1,
  "summary_field": "总评",
  "excellent_threshold": "90",
  "excellent_denominator": "all"
}
```

这组权重、封顶、奖励、绩点分段均是配置示例，不能代替真实课程规则。受限语法支持加减乘除、比较、布尔条件、field/state、min/max/sum/mean、best、ifelse、abs。禁止属性、导入、下标、循环、文件访问和任意模型Python代码。程序先结构检查与字段检查，再生成实际可审查的 calculation.py 和 rules.json，并用独立 `python -I -X utf8 calculation.py` 子进程执行。所有学生分数运算由Python Decimal完成；模型只提方案与表达式，不能直接返回分数当计算结果。

中间数学使用50位十进制精度，原分聚合的sum/mean优先聚合原分后折算，避免先做循环小数再累加。后续公式使用前序未舍入数值；precision仅决定最终导出格子的ROUND_HALF_UP舍入。不指定precision时不擅自选小数位。若教师要求绩点使用先舍入的总评，应先明确该规则；当前受限语法尚无中间round函数。

持久化边界：

```text
state/pipelines/<流程编号>/
  input/F1.original ...               # 原始上传字节
  workflow.json                      # 状态、当前版本、事件和历史试算
  dwd/v<版本>/
    governance.py                    # 生成并实际执行的阶段一Python
    inputs.json / plan.json          # 绑定哈希的解析输入和治理方案
    execution.json                   # 阶段一实际执行命令和代码/输入/计划哈希
    wide.csv                         # 每行一个编号，包含真实缺失状态
    data.json                        # 与CSV一致的结构及逐字段来源
    audit.json                       # 布局、归并、折算、身份、异常、逐条账本
    manifest.json                    # ready/blocked以及各文件SHA-256
  dws/t<试算编号>/
    rules.json                       # 本次完整业务规则
    calculation.py                   # 生成的确定性Python执行器和固定公式
    output.csv                       # 原项目＋业务结果＋处理状态/原因
    result.json                      # 结果、政策变化、摘要与运行异常
    execution.json                   # 实际命令、代码/规则/输出/阶段一哈希
    comparison.json                  # 有前次有效同DWD试算时生成
```

阶段二函数仅接收ready DWD的manifest，不接收平台文件。它检查manifest、CSV、结构化数据及审计的哈希和唯一编号，CSV与data.json也必须一致。公式文件绑定生成代码中的规则哈希；输出CSV与界面预览和保存哈希一致才允许下载。

仅修改业务规则时沿用同一DWD目录和哈希，新试算落入新目录。修改阶段一方案会生成新DWD版本，并清除基于旧DWD的当前结果；历史快照继续留审计。“实验权重提高10%”无法唯一确定百分点/相对比例及其余权重分配，所以先询问，再按完整权重执行；不会默认挪用其他项目权重。试算沙盒是数据与公式的隔离和受限表达式执行，不是承诺对任意Python提供操作系统级安全沙盒。

模型入口复用 model_config.example.json 和服务端密钥环境变量，启动方式与原通用版相同。未配置时界面明确禁用真实LLM；可以完整使用内置有限语法及手工JSON。真实模型建议也经过字段、公式和结构检查，且不能生成教师身份确认、排除决定或补分授权。默认只发送任务、字段与方案结构；人工身份/补分/排除记录不发给模型，最多3条样例只有allow_sample_values显式启用才发送。真实LLM连通性与语义质量本轮未测试。

实际运行与验收：

```powershell
# 独立生成测试数据与独立Python参考答案，不由正式入口调用
python -B -X utf8 ../multi-platform-grade-agent-support/development/tests/generate_two_stage.py
python -B -X utf8 ../multi-platform-grade-agent-support/development/tests/test_two_stage.py

# 另一个终端先启动app.py，再做真实浏览器验收
node ../multi-platform-grade-agent-support/development/tests/two_stage_browser.cjs

# 同一个流程也可由CLI调用
python -X utf8 pipeline_cli.py --task '实验40%，作业60%，未交记0，保留一位小数' --file ../multi-platform-grade-agent-validation/cases/05_双阶段三人及异常变体/输入文件/roster.csv --file ../multi-platform-grade-agent-validation/cases/05_双阶段三人及异常变体/输入文件/practice.csv --file ../multi-platform-grade-agent-validation/cases/05_双阶段三人及异常变体/输入文件/assignment.csv --state-dir ../multi-platform-grade-agent-support/artifacts/work/two_stage_demo
```

运行前预期见 ../multi-platform-grade-agent-validation/references/TWO_STAGE_EXPECTATIONS.md。实际结果见历史support/history/outputs/two_stage_acceptance.md、outputs/two_stage_acceptance.json，以及浏览器报告 outputs/two_stage_browser.json。Python验收真实启动HTTP服务和生成的Python子进程，没有模拟计算；浏览器检查实际上传、卡点续算、试算、刷新、两阶段DOM/API与下载CSV逐字段一致。

实现定位：pipeline/ingest.py负责表头布局和明确默认策略；pipeline/dwd.py只做治理与物理落盘；pipeline/rules.py校验受限表达式和有限自然语言语法；pipeline/dws.py只读ready产物并实际执行计算；pipeline/service.py串联和管理卡点/版本/试算；pipeline/advisor.py复用真实模型配置提方案；web/pipeline.js实现独立界面；app.py挂接/api/pipelines接口。

当前限制：CSV格式与有限别名/布局识别；latest时间需ISO格式；姓名模糊候选不会自动确认；考勤必须明确sum/mean；复杂自然语言在未配置LLM时需手工受限公式；不支持任意Python、任意文档、跨平台API或生产多用户身份审批。真实平台数据、大规模并发、真实LLM质量尚未验证。
