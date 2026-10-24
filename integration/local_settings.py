"""Local desktop settings. Credentials never become public model configuration."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FIELDS = {"enabled", "protocol", "endpoint", "model", "api_key_env", "allow_sample_values", "timeout_seconds"}


class SettingsError(ValueError):
    pass


def validate_key(key):
    if not isinstance(key, str) or not key.strip() or len(key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise SettingsError("密钥为空或包含无效字符，请重新输入；不要填写占位说明文字")
    return key


def validate_config(config):
    if not isinstance(config, dict) or set(config) - PUBLIC_FIELDS:
        raise SettingsError("模型配置包含不支持的字段；密钥只放本次输入或secrets.local.json")
    value = dict(config)
    for flag in ("enabled", "allow_sample_values"):
        if flag in value and type(value[flag]) is not bool:
            raise SettingsError("模型启用及样例发送选项须为布尔值")
    if value.get("protocol", "chat_completions") != "chat_completions":
        raise SettingsError("当前仅支持非流式Chat Completions兼容协议")
    if not isinstance(value.get("endpoint"), str) or not isinstance(value.get("model"), str) or not value["model"].strip():
        raise SettingsError("请填写完整接口地址和模型名称")
    try:
        endpoint = urllib.parse.urlsplit(value["endpoint"])
        _ = endpoint.port
    except ValueError:
        raise SettingsError("接口地址格式无效") from None
    if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment or any(c.isspace() for c in value["endpoint"]):
        raise SettingsError("接口地址不能含账号、查询参数、片段或空白；密钥只通过请求头发送")
    if not endpoint.hostname or not (endpoint.scheme == "https" or endpoint.scheme == "http" and endpoint.hostname in ("127.0.0.1", "localhost")):
        raise SettingsError("接口须使用HTTPS，或本机127.0.0.1/localhost的HTTP地址")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value.get("api_key_env", "INTEGRATION_MODEL_API_KEY")):
        raise SettingsError("密钥环境变量名无效")
    timeout = value.get("timeout_seconds", 30)
    if type(timeout) not in (int, float) or not 1 <= timeout <= 60:
        raise SettingsError("模型超时须为1至60秒")
    return value


def atomic_json(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".local-setting-", suffix=".tmp", delete=False) as handle:
            name = Path(handle.name)
            json.dump(content, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(name, path)
    except OSError:
        raise SettingsError("本机配置无法写入，请检查目录权限；原配置未被替换") from None
    finally:
        if name and name.exists():
            name.unlink()


def read_config(path=None):
    path = Path(path or ROOT / "config/model_config.local.json")
    if not path.exists():
        # Template is configuration, never a simulated model reply.
        return json.loads((ROOT / "model_config.example.json").read_text(encoding="utf-8"))
    try:
        if path.stat().st_size > 64 * 1024:
            raise ValueError
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        raise SettingsError("模型配置无法读取或JSON无效；未覆盖原文件，请在控制台修正或使用手动模式") from None
    return validate_config(value)


class SecretStore:
    def __init__(self, root=ROOT):
        self.root = Path(root).resolve()
        self.path = self.root / "config/secrets.local.json"

    def tracking_status(self):
        if not (self.root / ".git").exists():
            return "not_repository"
        git = shutil.which("git")
        if not git:
            raise SettingsError("无法检查Git跟踪状态，暂不保存密钥；仍可选择仅本次使用")
        result = subprocess.run([git, "-C", str(self.root), "ls-files", "--error-unmatch", "--", "config/secrets.local.json"],
                                capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode == 0:
            return "tracked"
        if result.returncode == 1:
            return "untracked"
        raise SettingsError("无法核对Git状态，未写入密钥文件")

    def check_safe(self):
        if self.tracking_status() == "tracked":
            raise SettingsError("密钥文件已被Git跟踪，拒绝写入或使用；请先移出版本控制。控制台不会自动改动Git索引")

    def read(self):
        self.check_safe()
        if not self.path.exists():
            return {}
        try:
            if self.path.stat().st_size > 64 * 1024:
                raise ValueError
            doc = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(doc, dict) or set(doc) != {"version", "keys"} or doc["version"] != 1 or not isinstance(doc["keys"], dict):
                raise ValueError
            for name, key in doc["keys"].items():
                if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                    raise ValueError
                validate_key(key)
            return doc["keys"]
        except (OSError, ValueError):
            raise SettingsError("本机密钥文件格式无效，未使用其内容；可仅本次输入或按说明修正文件") from None

    def save(self, env_name, key):
        validate_config({"endpoint": "https://local.invalid/chat/completions", "model": "validation", "api_key_env": env_name})
        value = self.read()
        value[env_name] = validate_key(key)
        atomic_json(self.path, {"version": 1, "keys": value})

    def clear(self, env_name):
        value = self.read()
        value.pop(env_name, None)
        if value:
            atomic_json(self.path, {"version": 1, "keys": value})
        elif self.path.exists():
            try:
                self.path.unlink()
            except OSError:
                raise SettingsError("无法清除本机密钥文件，请检查写权限") from None


def choose_key(source, config, store, typed="", environ=None):
    """An explicit UI source overrides the default typed > environment > saved priority."""
    env = os.environ if environ is None else environ
    name = config.get("api_key_env", "INTEGRATION_MODEL_API_KEY")
    if source == "input":
        return validate_key(typed)
    if source == "environment":
        key = env.get(name, "")
        if not key:
            raise SettingsError("服务端环境变量未设置；请选择本次输入、本机已存或手动模式")
        return validate_key(key)
    if source == "saved":
        key = store.read().get(name, "")
        if not key:
            raise SettingsError("本机尚未保存当前接口的密钥；请重新选择来源")
        return key
    if source == "none":
        return ""
    raise SettingsError("请选择明确的密钥来源")


def cli_config(path=None):
    """CLI keeps environment compatibility; only falls back to the explicit local secret file."""
    config = read_config(path)
    name = config.get("api_key_env", "INTEGRATION_MODEL_API_KEY")
    if os.environ.get(name):
        return config, "服务端环境变量（优先于本机文件）"
    key = SecretStore().read().get(name)
    if key:
        os.environ[name] = key
        return config, "本机密钥文件（明文保存）"
    return config, "未设置密钥；可使用手动流程"
