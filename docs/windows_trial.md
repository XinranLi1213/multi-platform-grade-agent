普通教师请优先双击启动控制台.vbs，操作及本机明文密钥保存方式见[桌面控制台](desktop_console.md)。原start.cmd和下述环境变量方式继续保留。

Windows独立试运行说明（0.2.0）

这是一台电脑一个本地服务的试运行版。另一台电脑独立启动自己的127.0.0.1页面；不是远程访问原电脑，不需要开放防火墙。正式程序仅依赖Python标准库和浏览器。

启动：

1. 安装或使用已有Python 3.11及以上。无需Node、VS Code、Git或pip安装依赖；Git只用于源码克隆。
2. 将源码包解压到有写权限的普通文件夹，支持中文和空格路径。阅读本说明后双击start.cmd。保留控制台窗口，Ctrl+C停止。
3. 启动器检查Python、核心文件、运行目录和端口，成功后打开 http://127.0.0.1:8765/pipeline 。端口占用可在终端运行start.cmd --port 8767。
4. 首次不配置模型也能试用。上传独立验证资料包 cases/08_Windows手动试运行/输入文件中的两份CSV，复制案例目录“任务提示词.txt”的任务。应得到86.0、54.0、32.0；编号前导零、0和原始未交状态保留。
5. 查看摘要、必要的问题、结果与下载。中文表单用于纠正和补充，普通流程不依赖JSON。

源码克隆后也可运行：

```powershell
python -u -X utf8 tools/launch.py
# 或仅检查环境，不启动、不请求外部模型
python -X utf8 tools/doctor.py --port 8767
```

目录区分：

| 目录/文件 | 用途 |
| --- | --- |
| app.py、pipeline、integration、web、tools | 版本化源码，不保存密钥或用户成绩 |
| model_config.example.json | 无密钥配置模板，默认禁用模型 |
| config/model_config.local.json | 本机配置，不纳入Git或分发包；必须由使用者创建 |
| 独立验证资料包 cases/08_Windows手动试运行/输入文件 | 明确标注的合成示例，正式入口不自动读取 |
| runtime/state | 一键启动后的原始上传、方案、两阶段产物和审计 |
| RELEASE_MANIFEST.json | 分发版本和文件SHA256清单 |

原开发入口app.py默认使用项目下state目录；新启动器默认使用runtime/state。可通过--state-dir明确指定。未提交浏览器草稿保存在当前浏览器，不跨电脑同步。

真实模型配置：

将model_config.example.json复制到config/model_config.local.json，填写enabled=true、完整endpoint、model；保留protocol=chat_completions、api_key_env=INTEGRATION_MODEL_API_KEY。支持非流式Chat Completions兼容HTTP JSON：Bearer请求头、model/messages请求、choices[0].message.content文本返回。其他厂商原生协议、流式或原生工具调用不在本轮。

只允许HTTPS或本机localhost/127.0.0.1的HTTP地址；不跟随重定向。不要把密钥放进URL、配置、命令参数、前端、日志或源码。配置中的api_key/key/token/authorization字段会被拒绝。

可以在PowerShell用隐藏输入设置本次进程环境，再从同一个终端启动：

```powershell
$trialSecret = Read-Host '输入模型密钥（隐藏输入）' -AsSecureString
$env:INTEGRATION_MODEL_API_KEY = [System.Net.NetworkCredential]::new('', $trialSecret).Password
python -u -X utf8 tools/launch.py
```

默认密钥从服务端环境变量或控制台本次输入获得；只有明确选择记住时，才写secrets.local.json明文文件。关闭终端后该设置不作为源码保存；服务已启动时修改父终端环境变量不会更新子进程，需要重启。不要将真实密钥写成命令字面量。

页面展开“模型连接与手动回退”，点击“检查真实模型连接”。只发送固定连接JSON，不发送教师文件。结果区分配置、连接、协议、业务方案契约；连接通过不代表真实任务理解质量已验证。也可以主动运行：

```powershell
python -X utf8 tools/doctor.py --port 8767 --check-model
```

检查401/403时核对密钥或账号权限，429时稍后重试，超时/连接失败时核对网络、地址或证书；不自动更换服务商。原始任务与手动流程保留，点击“使用中文表单”继续。配置错误、JSON错误均会明确显示，不伪装接入成功。

模型只理解任务、提出字段和计分方案。程序独立检查真实字段、规则、版本及人工裁决。结构或字段校验失败最多给模型一次受控修正机会；不能据此补造依据、确认身份或执行任意代码。明确业务歧义仍交给教师。

迁移旧电脑数据：

- 原始CSV、结果CSV、审计和已知规则可复制归档，不包含密钥。第一版使用原始CSV和规则在新电脑重新运行，不支持直接恢复完整历史会话。
- 旧workflow.json、生成Python、命令、执行目录和调整基准包含原机器绝对路径。检测到外部目录引用时仅显示“历史状态需在本机重跑”，拒绝继续和旧结果下载，不执行旧脚本，不替换路径或关闭哈希校验。
- DWD的wide.csv、data.json、audit.json和manifest可以原字节保留。当前没有便携导入入口；默认重新生成两阶段产物和本机哈希。
- 局部调整必须保留原判断快照、命中范围和规则版本。新电脑不能把“调整前”自动换成另一份结果；需要重新建立明确基准后再执行。
- 同位置的合法旧状态仍可读取；引擎版本或源码换行改变时，不保证旧生成脚本可重新执行，应生成新版本，历史审计不覆盖。

分发与验收边界：

源码包从已提交Git版本的白名单生成，排除实际配置、环境文件、state/runtime/work/outputs、日志及真实用户数据；构建脚本拒绝缺文件和白名单源码未提交修改。示例和文档可直接复制，真实API密钥必须在目标电脑单独配置。

本轮自动化可验证本机不同目录、中文空格路径、干净克隆和解压包。真实第二台Windows电脑和外部模型质量只有实际运行后才能称为通过；没有服务时必须标记未验证。此版本不支持远程访问、生产多用户、教务平台发布或其他文件格式。


验证案例已与正式程序分开，请另解压验证资料包并按其README选择CSV。正式启动不依赖support或validation。未提交代码将阻止正式构建，不绕过。新开发命令见project_layout.md。
