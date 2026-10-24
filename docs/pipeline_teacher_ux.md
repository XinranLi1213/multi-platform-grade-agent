教师数据工作台：交互与验证

正式入口仍为Python标准库的app.py，页面为原生HTML/CSS/JavaScript。没有新增Web框架、文件格式或平台API。DWD与DWS仍由各自生成的确定性Python执行器独立执行，CSV、manifest、原始字节、审计和历史版本继续保留。

```powershell
# 在解压后的正式程序目录执行
python -B -u -X utf8 tools/launch.py
# 打开 http://127.0.0.1:8765/pipeline
```

教师操作：

1. 在“你希望如何处理这些数据？”中描述目标、归并策略和明确权重，选择2至6份CSV，点击“整理并计算”。初始化恢复完成前输入暂不可编辑，防止旧任务覆盖刚填写的新任务。
2. 查看中文规则摘要和整理宽表；展开“查看文件实际读取结果与原值”核对真实表头、行数和原值。空缺、0与前导零分别保留。
3. 如有身份问题，卡片展示原文件和物理行、实际姓名/编号/班级、所有候选人的编号/班级/联系方式。没有默认候选。选择有效编号并填写核对依据，使用卡片内按钮提交。仅填写“已确认”不能代替选择编号。“继续待确认”保留记录并阻止阶段二，其他草稿不受单条提交影响。
4. 缺少时间时，卡片说明哪次提交缺时间、为什么不能确定最后有效提交，并提供“补充或修正提交时间”“明确裁决采用的成绩”“继续待确认”三种操作。补充时间须含日期和时分并填写核对依据；Python重新排序，保留原空时间、补充值和出处。日期本身不推断为零点。直接裁决则填写明确成绩或允许的特殊状态；“不填”不作为成绩提交。选择待确认会隐藏、禁用成绩和时间输入，按钮明确为“保存待确认，暂不计算”。相同备注重复提交不增加版本和审计；历史重复备注仅合并显示，完整审计不删除。主表外记录可明确追加或排除，均需依据。
5. 需要改变整理方式时，展开“调整数据整理方式”。按文件修改表头、身份字段、项目、策略和分制；时间字段仅在latest策略下显示。选择项取自实际表头。填写依据，先查看变更及影响，再确认重新处理。没有实际变更不生成新DWD；变化后旧计算失效并基于新DWD重算。
6. 调权重、特殊状态、小数位、封顶和优秀率使用“计算规则”表单。数据尚待核对时，按钮为“保存计算规则，待数据核对后执行”：后台先验证并持久化新规则，核对完成后自动采用。刷新恢复已保存的规则，错误时保留尚未保存的表单草稿；收到后端保存确认后才清除草稿。阶段一已完成时则可直接计算。百分比由Python Decimal转换。仅更改规则时DWD字节保持不变；重复保存相同规则也不增加版本。追加完整权重指令同样可先保存；无法解析、权重冲突或不合计100%时明确拒绝，不覆盖上一套已保存规则。
7. 下载当前CSV并核对结果。页面预览与导出共用当前执行结果；下载前再次检查哈希和CSV内容。刷新恢复流程、确认记录和本机草稿。JSON、Python命令、哈希、磁盘路径及故障诊断默认收起。

数据与执行条件：

| 功能 | 实现位置 |
| --- | --- |
| HTTP上传、读取状态与操作接口 | app.py，/api/pipelines系列接口 |
| CSV严格读取、表头/物理行/原值 | pipeline/ingest.py：parse_file、layout |
| 表单实际列选项、方案结构和人工裁决预检 | pipeline/interaction.py：tables、validate_plan、presentation |
| 逐条身份/值/主表外记录处理、版本与审计 | pipeline/service.py：resolve、resume、check、save、load |
| 变更摘要、无变化判断和确认绑定 | interaction.py：plan_diff、token；service.py：preview_plan、apply_plan |
| 同平台归并、跨平台身份、分制、来源落盘 | pipeline/dwd.py：materialize、aggregate、normalize_score |
| 真实模型建议与校验 | pipeline/advisor.py；复用model_config配置与服务端密钥环境变量，模型不能提交人工裁决 |
| 权重百分比转换、常见规则/表达式/状态检查 | service.py：prepare_rules、check_rules；pipeline/rules.py |
| ready宽表边界和阶段二Python执行 | pipeline/dws.py：read_dwd、generate_and_run、execute_file |
| 结构化可纠正输入错误/未知程序错误 | common.py：InputProblem、ExecutionFault；service.py：checkpoint、fault；app.py错误响应 |
| 卡片、表单、中文状态、错误保留草稿、按钮防重复 | web/pipeline.js：drawCards、drawGovernance、drawRules、action、render |
| 刷新恢复草稿 | pipeline.js：localStorage按流程与原始记录标识保存；服务器workflow.json保存已提交状态 |
| 预览与当前版本导出 | pipeline.js：table；service.py：download（哈希及CSV逐字段校验） |

后端独立检查源record_id确实存在、编号来自实际主表、核对依据非空、revision仍有效。单纯绕过前端或在JSON写“已确认”无法跳过条件。方案表单以原完整配置为基础更新显示字段，保留未展示的合法配置；JSON与已有表单草稿冲突时拒绝覆盖。

可纠正问题会给出问题类型、中文原因、原始记录/字段、影响、补充内容和可执行操作。未知子进程错误、哈希完整性问题或其他未知异常归类为系统故障，停止显示当前计算结果，提供重试，技术细节留在折叠诊断区。failure.json仍作为内部产物保存；不再要求教师寻找文件。

实际验收命令（测试生成器与入口完全独立）：

```powershell
python -B -X utf8 ../multi-platform-grade-agent-support/dev.py verify-data
python -B -X utf8 ../multi-platform-grade-agent-support/development/tests/test_teacher_pipeline.py
python -B -X utf8 ../multi-platform-grade-agent-support/development/tests/test_two_stage.py
python -B -X utf8 ../multi-platform-grade-agent-support/development/tests/test_interactive.py

# 独立终端启动真实浏览器验收服务；仅此测试启动器支持一次性未知故障注入
python -B -u -X utf8 ../multi-platform-grade-agent-support/dev.py serve-browser
$env:INTEGRATION_TEST_URL = 'http://127.0.0.1:8766'
node ../multi-platform-grade-agent-support/development/tests/two_stage_browser.cjs
node ../multi-platform-grade-agent-support/development/tests/checkpoint_browser.cjs

# 原通用CSV工作台的浏览器回归，使用正式8765服务
$env:INTEGRATION_TEST_URL = 'http://127.0.0.1:8765'
node ../multi-platform-grade-agent-support/development/tests/browser_acceptance.cjs
```

案例已统一为独立验证资料，不再定位桌面；原来源记录仍保留。测试读取独立验证资料README中的01、02、03各组输入文件，原文件运行前后SHA-256保持一致。其余冲突与主表外案例读取原独立../multi-platform-grade-agent-validation/cases/05_双阶段三人及异常变体/输入文件模拟CSV。正式app.py不导入测试数据或故障注入器。

完整逐项预期、实际结果、产物位置与未运行项见 [本轮实际报告](../../multi-platform-grade-agent-support/history/outputs/teacher_ux_acceptance.md)。历史机器报告：support/history/outputs/teacher_backend_acceptance.json、outputs/two_stage_browser.json、outputs/two_stage_acceptance.json、outputs/interactive_acceptance.json、outputs/browser_acceptance.json。截图仅辅助视觉核对，验收依据还包括真实点击、HTTP状态、版本变化和实际下载内容。

待确认与规则保存修复的最新专项报告：[checkpoint_fixed_acceptance.md](../../multi-platform-grade-agent-support/history/outputs/checkpoint_fixed_acceptance.md)。专项浏览器脚本实际覆盖8组诊断操作，另有后端校验、补时间排序和审计检查。人工时间补充保存在plan.timestamp_resolutions，原上传与原始values不修改；实际转换进入阶段一audit.json的conversions。

当前限制与未运行：没有配置真实LLM，因此真实模型联网与语义质量未运行；没有使用固定回复冒充模型。复杂条件仍使用高级受限公式JSON或真实模型建议，常见权重/状态/归并不依赖JSON。仅CSV、2至6份、单份2 MiB，latest时间需ISO格式；未扩展Excel、PDF、平台API、多用户权限或大数据并发。浏览器实际验证采用Windows Edge；其他浏览器、移动端和真实教学生产数据未验证。来源在卡片及完整审计中，主宽表维持每行一个编号而不把所有提交来源塞进单个CSV格子。
