在本目录创建 model_config.local.json，可从项目根目录的 model_config.example.json 复制。

只填写enabled、protocol、endpoint、model、api_key_env、allow_sample_values、timeout_seconds。
优先使用项目根目录的“启动控制台.vbs”配置接口并输入密钥。默认仅本次使用，不写文件。
明确选择记住时写本目录secrets.local.json（明文，未加密）；手动格式及清除方法见docs/desktop_console.md。
保留服务端环境变量方式。命令行环境变量优先、本机文件回退；控制台以明确选择的来源为准。
密钥不得写入普通model_config.local.json、网页、日志、命令参数或源码。secrets.local.json必须排除Git和所有分发包。
未配置时直接启动，中文手动表单仍然可用。新配置在重启服务后生效。
