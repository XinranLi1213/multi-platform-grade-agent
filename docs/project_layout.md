# 目录、开发与分发

正式项目保留运行代码、原生前端、启动器、环境检查、配置模板、精选合成体验案例、用户说明及版本管理文件：

```text
multi-platform-grade-agent/
  app.py pipeline_cli.py grade_agent.py
  pipeline/ integration/ web/
  tools/                 __init__.py、launch.py、doctor.py及桌面控制台/受管服务/窗口模块
  config/ docs/ .vscode/ .git/
  start.cmd VERSION README.md model_config.example.json
  state/ runtime/        受保护的历史状态及新状态
  work/portable-server.*.log  正式服务仍占用，暂不迁移
```

项目旁的 `multi-platform-grade-agent-support/development` 保存唯一活动开发源码，`history` 保存原字节输出、日志、开发副本及嵌套Git，`migration` 保存原路径、目标、用途、Git来源、SHA、复制核对、原工作树和未提交差异。历史执行状态含绝对路径和引擎哈希，只归档，不修改后声称迁移成功。用途不明的state/runtime全部原位保留。

`multi-platform-grade-agent-validation` 保存已有合成验证案例。教师看该目录README，按顺序选择同一案例的CSV和任务；不要混传不同版本或异常变体。事先预期与历史实际分开。全部原输入保留字节、来源及SHA；没有可靠数值预期的不提供预期CSV。

正式程序不导入support配置，不自动读取验证资料。无需Node、Playwright、VS Code或外部辅助目录。

开发路径只在 `support/paths.json` 配置：source、validation、artifacts均相对support。可用 `GRADE_AGENT_SOURCE_ROOT`、`GRADE_AGENT_SUPPORT_ROOT`、`GRADE_AGENT_VALIDATION_ROOT`、`GRADE_AGENT_ARTIFACT_ROOT` 覆盖。所有新测试状态、下载、报告、生成数据、分发产物写support/artifacts；指向正式项目会拒绝。

在support目录执行，也可使用dev.py绝对路径：

```powershell
python -B -X utf8 .\dev.py verify-data
python -B -X utf8 .\dev.py test all
python -B -X utf8 .\dev.py generate two-stage
python -B -X utf8 .\dev.py generate generic
python -B -X utf8 .\dev.py generate legacy
python -B -X utf8 .\dev.py generate teacher
python -B -X utf8 .\dev.py serve-browser
# 另一终端；仅开发浏览器测试需要Node、Playwright、Edge
python -B -X utf8 .\dev.py browser all
python -B -X utf8 .\dev.py isolated
python -B -X utf8 .\dev.py package runtime --output .\artifacts\dist\runtime.zip
python -B -X utf8 .\dev.py package validation
```

生成器总是写新目录，不覆盖整理后的验证库。测试不自动生成数据。浏览器测试默认独立8766，不对正式8765写入。Python隔离阶段使用-B，避免向源码写缓存。

正式运行包只从Git已提交白名单blob构建；有未提交或未跟踪运行源码时拒绝，不自动提交。白名单包含旧CLI及全部运行依赖、RELEASE_MANIFEST.json逐文件SHA，排除tests/开发生成器/状态/实际配置/密钥/缓存/日志/Git。公开精选examples及标准库构建与冒烟工具可随包分发。验证资料包独立校验FILE_MANIFEST及来源清单。

`isolated`只验证当前工作树白名单副本，不生成正式程序ZIP、不替代Git检查。正式包解压验收、另一台物理电脑和真实模型分别标记实际结果。

原正式仓库会显示已迁跟踪文件删除，尚未提交；外置development的SOURCE_ORIGIN.json和迁移快照保留版本来源，后续可另纳入维护仓库，本轮不自动建仓、提交或推送。回滚先运行support的rollback工具dry-run；仅在内容仍匹配本轮记录时恢复，禁止覆盖后续新改动，不使用git reset/clean。

桌面入口为启动控制台.vbs及启动控制台.cmd，start.cmd命令行入口保留。secrets.local.json（明文）默认不创建，只有明确记住才保存，排除Git和所有包；详细说明见desktop_console.md。

GitHub发布版提供tools/build_package.py，可独立从已提交白名单构建（不依赖support）；运行 python -B -X utf8 tools/build_package.py --output 项目外输出.zip。普通使用者可运行 python -B -X utf8 tools/verify_demo.py 验证公开基础案例。历史开发目录说明保留供原维护环境使用，克隆本仓库无需这些目录。
