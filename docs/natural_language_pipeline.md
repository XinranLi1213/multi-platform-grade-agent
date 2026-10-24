自然语言教师整合与计分：已实现的最小版本

启动：

```powershell
# 在解压后的正式程序目录执行
python -B -u -X utf8 tools/launch.py
```

访问 http://127.0.0.1:8765/pipeline 。沿用Python标准库和原生HTML/CSS/JavaScript，无生产前端依赖。

页面操作：

1. 在“你希望如何处理这些数据？”一次说明整理、项目组成、权重及状态政策，上传2至6份CSV。程序先真实读取，再理解；原文件不覆盖。
2. 配置真实模型时，自动发送任务、真实表头、行数、空缺/重复/数值范围/时间可解析性/候选键统计。仅allow_sample_values=true时发送每表最多3条实际样本。范围只是观测统计，不能证明满分或业务含义。
3. 指令明确、引用有效且校验通过，自动完成DWD→独立宽表→DWS，不再重复配置第二阶段。有歧义时只显示对应问题，补充后仍使用已上传数据。
4. “修改理解”默认折叠，可用中文表单定义任意名称的考核项目、组成方式、权重、状态政策及局部调整。JSON仅供高级操作。
5. 后续在同一任务框追加指令。“执行追加指令”更新实际方案；“试算并查看变化”产生独立试算。指令包含“试试看”“看看变化”“先试算”“试算一下”也采用试算模式。试算不替换已采用结果，点击“采用本次试算结果”后切换。

真实模型配置沿用model_config.example.json：复制到config/model_config.local.json，设置enabled=true、服务商Chat Completions兼容endpoint和model。api_key_env指定密钥环境变量，密钥不写入页面或日志；可通过app.py --model-config指定配置路径。正式程序没有fixture或假模型回复。

未配置模型时仅支持明确标注的有限自然语言语法，例如完整项目权重和既有状态语法；不承诺自动理解任意调分指令。自定义项目和调整的完整手动流程仍可运行。读取、校验和成绩运算不依赖模型。

方案与执行边界：

| 层面 | 实现与边界 |
| --- | --- |
| 读取与特征 | pipeline/ingest.py真实解析；pipeline/semantic.py的profiles生成统计、原字段引用和候选键检查。样本来自真实行，不合成模型输入 |
| 模型方案 | pipeline/advisor.py提出stage1_plan、stage2_rules或stage2_draft、questions、rationale、evidence；来源字段、公式及人工动作独立校验。自动执行的变更层面需要引用已保存本次教师指令的真实原文；证据引用不代表模型可以确认身份 |
| 不完整理解 | stage2_draft单独保存已明确项目及权重，不作执行输入。后续模型请求携带草稿、已知决定和指令记录，只补缺项 |
| 阶段一 | pipeline/dwd.py清洗、身份关联、同一业务活动的重复提交归并、分制处理。record_grain标识活动字段；where以真实活动原值分列，不把不同展示归成一次提交 |
| 阶段二项目 | assessment_items独立于DWD列，包含id、name、sources、operation、basis及参数。sources引用真实测量项或前序项目ID；支持direct/mean/sum/max/best/weighted及封顶。累加需明确合成满分，内部权重合计100%；项目结果归一至百分制 |
| 阶段二规则 | pipeline/rules.py将项目依赖排序，拒绝循环/未知字段，编译为已有受限公式；pipeline/dws.py生成并执行独立Python，使用Decimal，舍入只用于展示输出 |
| 局部调整 | adjustments包含id、baseline、scope、target、action、value、special、basis、可选cap/order/preserve。范围为明确文本编号或一个数值条件；动作支持add/multiply/set/floor；不能直接覆盖总评公式 |
| 版本与恢复 | pipeline/service.py保存workflow.json、semantic/v版本.json、指令、决定、实际规则和版本。旧状态兼容读取；浏览器localStorage独立保留未提交草稿 |
| 导出 | 预览来自同一结果rows/columns；下载核对CSV字节、结果结构及哈希。完整审计包含DWD的cell_sources/原始记录和业务规则、命中范围及变化 |

固定基准与不累计：

“before_adjustment”绑定本次范围使用的调整前快照；无已有结果时由Python先计算未调整结果。保存frozen_baseline.json、adjustment_scope.json、adjustments.json，后续同一调整流程继续基于该快照筛选，不因调分后跨过及格线而重新选择。若要改用另一次结果，应显式选择其计算版本。不同DWD版本的基准不能沿用。

调整始终重新计算业务输入及依赖公式，原始列不覆盖；输出另有“调整前/调整后”列，审计保存每条规则命中编号、前后值及依据。首次任务直接带调整也会保存本次调整前后差异。多条规则命中同一学生时保守要求明确且不同的执行顺序，不自行选择。

同一request_id重复提交不执行第二次；同一指令在方案与数据上下文未变化时复用结果。表单再次执行从原始DWD和绑定基准计算，不在上次调整后的单元格上累计。明确新增顺序规则才会执行多次动作。

只改变项目/权重/状态/调整复用DWD；改变表头/关联/归并/分制重新生成DWD，并使当前旧DWS及调整基准失效。旧文件保留历史审计。新执行失败时不把旧结果冒充本次结果；此前已采用结果单独标注。

当前限制：

- 仅CSV，沿用每文件2MiB、5100条记录等原有限制；不接其他平台或格式。
- 最多16个组合项目、12条调整及20条编译公式；来源组合支持依赖项目ID。
- 范围条件第一版为一个数值比较或编号列表；一批调整采用同一判断基准。重叠范围保守要求顺序，即使某些动作数学上可交换也不会自行推断。
- 阶段一活动筛选表单一次编辑一个条件，已有其他合法条件保留；复杂多条件可使用高级方案。缺失活动或提交时间不猜测。
- 模型理解质量取决于真实服务；本轮只验证明确标注的本机传输fixture，未进行外部真实LLM质量、网络连通性或大规模性能验收。
- 未提交草稿保存在当前浏览器，不是跨设备同步。采用/试算用于本地版本管理，不代表向教务平台发布。

验收脚本独立于正式程序：

```powershell
python -B -X utf8 ../multi-platform-grade-agent-support/development/tests/test_semantic_pipeline.py
python -B -X utf8 ../multi-platform-grade-agent-support/development/tests/test_two_stage.py
python -B -X utf8 ../multi-platform-grade-agent-support/development/tests/test_teacher_pipeline.py
python -B -X utf8 ../multi-platform-grade-agent-support/development/tests/test_interactive.py

# 独立测试服务，测试状态在support/artifacts/work/teacher-browser-state
python -B -u -X utf8 ../multi-platform-grade-agent-support/dev.py serve-browser
# 另一个终端执行
node ../multi-platform-grade-agent-support/development/tests/semantic_browser.cjs
node ../multi-platform-grade-agent-support/development/tests/two_stage_browser.cjs
node ../multi-platform-grade-agent-support/development/tests/checkpoint_browser.cjs
$env:INTEGRATION_TEST_URL = 'http://127.0.0.1:8766'
node ../multi-platform-grade-agent-support/development/tests/browser_acceptance.cjs
```

浏览器测试使用本机Edge及已有Playwright测试运行时，非正式程序依赖。测试覆盖真实按钮提交、状态变化、刷新、版本及下载，截图仅作辅助。

模拟输入：../multi-platform-grade-agent-validation/cases/07_自定义项目与局部调整/输入文件/roster.csv、performance.csv。预先约定：两展示平均组成课堂展示40%、实践40%、平时20%、缓考保留；001/002/003/004原总评应分别48.0/86.0/0.0/缓考。将调整前总评低于60者平时表现设100，仅001和003命中，总评应为64.0/86.0/20.0/缓考，原始平时仍20/90/0/70。

详细预期、实际及未运行检查汇总在历史support/history/outputs/semantic_acceptance.md；逐项记录为semantic_backend_acceptance.json和semantic_browser_acceptance.json。桌面三组数据的原文件哈希由原有教师验收核对，不覆盖文件。
