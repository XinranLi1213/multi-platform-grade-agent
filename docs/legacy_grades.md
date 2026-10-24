# 旧版两平台计分 CLI

保留可离线运行的 grade_agent.py，Python 3.11+ 标准库。主程序只读取显式参数，不调用生成器、不读取默认模拟路径。

在正式程序目录执行，文件路径由使用者提供：

```powershell
python -B -X utf8 grade_agent.py --platform-a "<平台A.csv>" --platform-b "<平台B.csv>" --rules "<规则.json>" --output-dir "<新的输出目录>"
```

A表头为“学号、实验A成绩”，B为“学号、实验B成绩”；UTF-8支持BOM。编号按文本并集关联，0有效，空值不补0，重复编号保留原记录并挂起。输出summary.csv及audit.json，包含物理行号、原值、SHA、规则与逐人状态。

规则必须包含version、唯一ID、明确applicable、权重、舍入及source.file/source.line；材料路径相对规则JSON所在目录。冲突时不任选，补充resolution.selected_rule_id、teacher_confirmation及有效出处后续算。退出0表示产出汇总（可能有待处理行），读取失败返回2。续算加--previous-audit "<上次audit.json>"，输入SHA必须一致，使用新输出目录，保留前次审计，不补缺失成绩。

现有合成资料在独立validation/cases/04_基础计分及规则异常/输入文件。选择platform_a.csv、platform_b.csv、rules.json及同目录course_materials.txt，1001=86.0、1002=54.0、1003缺B且总分空。其他rules_*.json、异常CSV是独立变体，不要混配。

在support目录开发验证：

```powershell
python -B -X utf8 .\dev.py test legacy
python -B -X utf8 .\dev.py generate legacy
```

验收读取validation已有资料并真实调用CLI；生成器写support/artifacts/generated的新目录，不覆盖案例。验收默认support/artifacts/runs/legacy_时间戳，显式--output-root也必须在正式项目外且为空。报告在support/artifacts/reports。

实现位置：read_platform读取；associate_students关联；check_score区分空值/0/重复；resolve_rules/read_evidence检查来源和冲突；calculate_total使用Decimal；integrate串联导出及续算。真实平台数据、身份认证、外部模型及第二台物理电脑未在该CLI验收中验证。
