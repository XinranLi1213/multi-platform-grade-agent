在本目录创建 model_config.local.json，可从项目根目录的 model_config.example.json 复制。

只填写enabled、protocol、endpoint、model、api_key_env、allow_sample_values、timeout_seconds。
密钥只设置在启动服务进程的环境变量中，不能写在此目录、网页、日志或源码中。
未配置时直接启动，中文手动表单仍然可用。新配置在重启服务后生效。
