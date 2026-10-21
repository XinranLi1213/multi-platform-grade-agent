"""Standard-library, non-streaming Chat Completions transport. Never logs credentials/payloads."""
import json
import os
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

PROTOCOL = "chat_completions"
MAX_RESPONSE = 1024 * 1024
MAX_REQUEST = 512 * 1024


class ModelFailure(ValueError):
    def __init__(self, code, message, *, transport_ok=False, protocol_ok=False, http_status=None):
        super().__init__(message)
        self.code, self.message = code, message
        self.transport_ok, self.protocol_ok, self.http_status = transport_ok, protocol_ok, http_status

    def public(self):
        return {"status": "error", "code": self.code, "message": self.message,
                "transport_ok": self.transport_ok, "protocol_ok": self.protocol_ok,
                **({"http_status": self.http_status} if self.http_status is not None else {})}


def configuration(config, require_enabled=True):
    if config.get("_config_error"):
        message = "未找到模型配置文件；可复制模板后配置，手动流程仍可使用" if config["_config_error"] == "config_missing" else "模型配置文件无法读取或JSON格式错误，请修正后重启服务；手动流程仍可使用"
        raise ModelFailure(config["_config_error"], message)
    if any(k in config for k in ("api_key", "key", "token", "authorization")):
        raise ModelFailure("secret_in_config", "配置文件不能保存密钥；请改用服务端环境变量，并移除配置中的密钥字段")
    if set(config) - {"enabled", "protocol", "endpoint", "model", "api_key_env", "allow_sample_values", "timeout_seconds"}:
        raise ModelFailure("config_fields", "配置包含不支持的字段；请使用模板，密钥只放服务端环境变量")
    if require_enabled and config.get("enabled") is not True:
        raise ModelFailure("disabled", "语义分析尚未启用；可使用手动表单")
    if config.get("protocol", PROTOCOL) != PROTOCOL:
        raise ModelFailure("unsupported_protocol", "当前仅支持非流式Chat Completions兼容协议")
    endpoint = config.get("endpoint")
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ModelFailure("endpoint_missing", "请在服务端配置完整模型请求地址")
    if any(c.isspace() for c in endpoint):
        raise ModelFailure("endpoint_invalid", "模型地址包含空白字符，请填写完整且有效的请求地址")
    try:
        url = urllib.parse.urlsplit(endpoint)
        _ = url.port
    except ValueError:
        raise ModelFailure("endpoint_invalid", "模型请求地址格式无效") from None
    if url.username or url.password or url.query or url.fragment:
        raise ModelFailure("endpoint_credentials", "模型地址不能包含账号、查询密钥或片段；密钥只能从环境变量读取并通过请求头发送")
    if not url.hostname or not (url.scheme == "https" or url.scheme == "http" and url.hostname in ("localhost", "127.0.0.1")):
        raise ModelFailure("endpoint_invalid", "模型地址须为HTTPS或本机localhost/127.0.0.1的HTTP地址")
    if not isinstance(config.get("model"), str) or not config["model"].strip():
        raise ModelFailure("model_missing", "请填写实际模型名称")
    env_name = config.get("api_key_env", "INTEGRATION_MODEL_API_KEY")
    if not isinstance(env_name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name):
        raise ModelFailure("key_env_invalid", "密钥环境变量名格式错误")
    key = os.environ.get(env_name, "")
    if not key.strip():
        raise ModelFailure("key_missing", "服务端未设置密钥环境变量；请在启动服务的终端设置后重启，或继续手动处理")
    if any(ord(c) < 32 or ord(c) > 126 for c in key):
        raise ModelFailure("key_invalid", "密钥环境变量包含无效字符；请重新设置，不要将密钥写入配置或页面")
    timeout = config.get("timeout_seconds", 30)
    if type(timeout) not in (int, float) or not 1 <= timeout <= 60:
        raise ModelFailure("timeout_invalid", "模型超时须为1至60秒")
    return endpoint, key, timeout


def redact(value, key):
    """Round-trip JSON so both literal and escaped secret echoes are removed recursively."""
    if isinstance(value, str):
        return value.replace(key, "[REDACTED]")
    if isinstance(value, list):
        return [redact(v, key) for v in value]
    if isinstance(value, dict):
        return {redact(k, key): redact(v, key) for k, v in value.items()}
    return value


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward Authorization to another URL, including a different host.
        return None


def complete_json(config, messages):
    endpoint, key, timeout = configuration(config)
    body = json.dumps({"model": config["model"], "messages": messages}, ensure_ascii=False).encode("utf-8")
    if len(body) > MAX_REQUEST:
        raise ModelFailure("request_too_large", "理解上下文超过本轮上限；请缩小输入范围或使用手动表单，未静默截断数据")
    request = urllib.request.Request(endpoint, data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()  # Provider body may echo secrets; never read or log it.
        code, message = {
            401: ("authentication", "模型服务认证失败（HTTP 401）；请核对服务端密钥后重启，手动流程保留"),
            403: ("forbidden", "模型服务拒绝访问（HTTP 403）；请核对账号权限和服务设置"),
            429: ("rate_limit", "模型服务限流或额度限制（HTTP 429）；请稍后重试，也可手动处理")
        }.get(status, ("redirect_blocked", "模型地址发生重定向，未转发密钥；请配置最终请求地址") if 300 <= status < 400
              else ("http_error", f"模型服务返回HTTP {status}；本次未得到可执行方案，手动流程保留"))
        raise ModelFailure(code, message, transport_ok=True, http_status=status) from None
    except (TimeoutError, socket.timeout):
        raise ModelFailure("timeout", "模型请求超时；任务和上传保留，可重试或手动处理") from None
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, (TimeoutError, socket.timeout)):
            raise ModelFailure("timeout", "模型请求超时；可重试或手动处理") from None
        code = "tls" if isinstance(exc.reason, ssl.SSLError) else "connection"
        raise ModelFailure(code, "无法建立模型连接；请核对网络、地址及证书，任务和手动流程保留") from None
    except OSError:
        raise ModelFailure("connection", "模型网络连接失败；任务和手动流程保留") from None
    if len(raw) > MAX_RESPONSE:
        raise ModelFailure("response_too_large", "模型响应超过1MiB，未采用部分内容", transport_ok=True)
    try:
        envelope = json.loads(raw.decode("utf-8"))
        text = envelope["choices"][0]["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError
    except (ValueError, KeyError, IndexError, TypeError):
        raise ModelFailure("protocol", "返回不符合非流式Chat Completions协议（需要choices[0].message.content文本）", transport_ok=True) from None
    text = text.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    try:
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError
    except ValueError:
        raise ModelFailure("json", "模型内容不是JSON对象，未得到可校验方案", transport_ok=True, protocol_ok=True) from None
    return redact(value, key)


def check_connection(config):
    """A real opt-in request with no uploads. Connectivity does not prove business-plan correctness."""
    result = {"configuration_valid": False, "transport_ok": None, "protocol_ok": None,
              "plan_contract_ok": None, "checked_at": datetime.now(timezone.utc).isoformat(),
              "business_quality_verified": False}
    try:
        configuration(config)
        result["configuration_valid"] = True
        response = complete_json(config, [{"role": "system", "content": "连接检查，不读取文件、不计算成绩。只返回JSON对象：{\"connection_check\":\"ok\"}。"},
                                          {"role": "user", "content": "请返回连接检查JSON。"}])
        result.update(transport_ok=True, protocol_ok=True)
        if response != {"connection_check": "ok"}:
            raise ModelFailure("probe_contract", "连接和协议可用，但连接检查内容不符合约定；业务方案契约尚未验证", transport_ok=True, protocol_ok=True)
        result.update(status="ok", message="连接与JSON协议检查通过；业务方案及理解质量仍需真实任务验证")
    except ModelFailure as exc:
        result.update(exc.public())
        if not result["configuration_valid"]:
            result.update(transport_ok=None, protocol_ok=None)
    return result
