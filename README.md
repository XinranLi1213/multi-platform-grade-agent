两平台成绩整合最小版本

本地开发目录：`D:\programs\multi-platform-grade-agent`。在 VS Code 中打开 `grade-agent.code-workspace` 即可识别项目与 Git。通过“终端 → 运行任务”可运行示例或全部验收。运行产物 `runs/`、`reports/` 已加入 `.gitignore`，源代码、模拟输入和编辑器配置纳入版本管理。

```powershell
Set-Location 'D:\programs\multi-platform-grade-agent'
git status
python -X utf8 run_acceptance.py
code --new-window grade-agent.code-workspace
```

这是可离线运行的规则驱动流程。教师提供两个 CSV 和明确的结构化计分规则，程序按学号关联，保留原始分项成绩，有依据才计算，异常逐人待处理。Python 3.11 及以上，仅使用标准库，无需安装依赖。

本项目的CSV与课程材料均为模拟数据。正常批次规则是本轮给定的 40%/60%。冲突用的 50%/50% 只存在于单独验收配置。模拟教师确认记录仅用于验收，不代表实际教师已作出确认。

运行命令（PowerShell，在项目目录执行）：

```powershell
python -X utf8 make_fixtures.py
python -X utf8 grade_agent.py --platform-a inputs/platform_a.csv --platform-b inputs/platform_b.csv --rules inputs/rules.json --output-dir runs/baseline
python -X utf8 run_acceptance.py
```

make_fixtures.py 会重新生成 inputs 中的模拟文件；使用真实文件时直接执行 grade_agent.py。CLI返回0表示成功产出汇总，汇总中可以包含待处理学生；输入结构不合法等批次级错误返回2。验收脚本全部通过返回0，否则返回1。

输入、输出位置：

| 文件或目录 | 用途 |
| --- | --- |
| inputs/platform_a.csv | 模拟平台A导出，列为学号、实验A成绩，包含1003/1001/1002 |
| inputs/platform_b.csv | 模拟平台B导出，列为学号、实验B成绩，顺序与A不同 |
| inputs/rules.json | 正常规则，显式配置权重0.4/0.6与舍入口径 |
| inputs/course_materials.txt | 规则原文，引用行号并记录哈希；冲突材料明确标注为验收专用 |
| inputs/rules_conflict.json | 40%/60%和50%/50%两份候选规则均声明适用的模拟冲突 |
| inputs/rules_resolved.json | 保留两份规则，追加有出处的模拟教师适用说明 |
| inputs/teacher_confirmation.txt | 明确标注的模拟教师确认记录 |
| runs/baseline/summary.csv | 正常批次汇总，每人一行；原始成绩、总分、状态、原因、来源 |
| runs/baseline/audit.json | 原始记录、物理行号、文件SHA-256、规则版本、精确总分与异常 |
| runs/conflict/、runs/resolved/ | 冲突挂起及教师明确后续算的独立输出，前次文件保留 |
| runs/missing_rules/、runs/supplemented/ | 缺权重及补规则后续算的独立输出 |
| reports/acceptance_results.csv | 每项检查的输入、预期、实际和结果 |
| reports/acceptance_report.md | 人可读的实际验收报告与完整运行命令、未运行检查 |
| reports/execution_log.json | 验收检查、命令参数、退出码、stdout与stderr |

输入 CSV 使用 UTF-8，支持 BOM；生成的 CSV 使用 UTF-8 BOM 方便查看中文。学号全程保留为字符串，用两个文件中学号的并集确定人数。按学号匹配，不依赖行顺序。空单元格、纯空格或平台没有该学号记录均为缺成绩，0是合法数值。重复学号超出本轮唯一性假设，程序仍会保留所有记录并挂起，防止被字典覆盖后误选。

本轮使用可追溯的结构化JSON规则；自然语言材料到JSON由教师/开发者显式录入，程序检查文件出处可读取并保存原文，尚未验证JSON语义与原文的自动一致性。规则中的 applicable 必须显式为布尔值。未知适用性、缺少权重或来源、多条适用规则冲突均保留总分为空。decimal_places=1 与 ROUND_HALF_UP 沿用上轮测试约定，来源文字也注明该约定。计算使用 Decimal，最后一步舍入，不使用二进制浮点数。

规则冲突续算（先执行冲突批次，再执行教师确认批次）：

```powershell
python -X utf8 grade_agent.py --platform-a inputs/platform_a.csv --platform-b inputs/platform_b.csv --rules inputs/rules_conflict.json --output-dir runs/conflict
python -X utf8 grade_agent.py --platform-a inputs/platform_a.csv --platform-b inputs/platform_b.csv --rules inputs/rules_resolved.json --output-dir runs/resolved --previous-audit runs/conflict/audit.json
```

教师规则说明通过 resolution.selected_rule_id、teacher_confirmation 和 source 明确记录。单独提供规则ID无法解除冲突。此接口记录教师输入，不实现身份认证。续算要求两份成绩文件SHA-256与前次一致，并使用新目录保存输出；audit.json记录前次审计文件哈希及逐人状态变化。补充规则不提供缺失成绩，1003仍待处理。相同输入与规则重复运行得到逐字节一致的CSV及审计JSON。

实现位置：

| 环节 | grade_agent.py中的函数 | 行为 |
| --- | --- | --- |
| 读取 | read_platform | csv.reader读取文件、检查表头、保留原值和物理行号 |
| 关联 | associate_students | 两个平台学号集合取并集，按字符串学号查找两侧记录 |
| 空值检查 | check_score | 区分无记录、空字符串/空格与0，返回具体原因 |
| 规则检查 | resolve_rules / read_evidence | 检查明确适用性、权重完整性、冲突说明、材料位置与哈希 |
| 计算 | calculate_total | Decimal加权求和，最后按显式规则舍入 |
| 串联与续算 | integrate | 生成汇总及审计，对照上次文件哈希，保存规则与逐人变更 |

未运行：真实平台/真实课程文件验收；自然语言、PDF或OCR规则自动提取；学号、课程和学期的一致性真实性核验；教师身份认证及真实审批；平台API、LLM、图形界面及大规模性能测试。本版交付范围是离线CLI和可核对的整合闭环。
