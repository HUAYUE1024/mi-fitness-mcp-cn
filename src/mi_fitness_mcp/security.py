"""本地安全加固工具：Host 校验、CORS 解析与 API Key 密钥的 keyring 存取。

设计目标（见 docs/PRIVACY.md）：
- 浏览器中的恶意网页无法读取本地 API 的健康数据（CORS 默认仅本机来源）；
- DNS 重绑定攻击无法绕过同源策略（Host 头白名单）；
- 发放的 API Key 对应的 passToken 不落 SQLite 明文，存入系统 keyring
  （Windows DPAPI / macOS Keychain / Linux Secret Service），keyring 不可用时
  才回退到数据库列并保持兼容。
"""

import hashlib
import os

SERVICE_NAME = "mi-fitness-mcp"

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}


def default_allowed_hosts() -> set[str]:
    """Host 白名单。默认仅本机回环；环境变量 MI_FITNESS_ALLOWED_HOSTS 可覆盖（逗号分隔，* 放行全部）。"""
    env = os.environ.get("MI_FITNESS_ALLOWED_HOSTS")
    if env:
        return {h.strip().lower() for h in env.split(",") if h.strip()}
    return set(LOOPBACK_HOSTS)


def host_allowed(host: str | None, allowed_hosts: set[str]) -> bool:
    """校验请求 Host 头是否在白名单内（自动剥离端口，支持 IPv6 方括号写法）。"""
    if "*" in allowed_hosts:
        return True
    if not host:
        return False
    h = host.strip().lower()
    if h.startswith("["):
        end = h.find("]")
        if end != -1:
            h = h[: end + 1]
    elif ":" in h:
        h = h.rsplit(":", 1)[0]
    return h in allowed_hosts


def cors_origins_from_env(default_regex: str) -> dict:
    """CORS 配置：环境变量 MI_FITNESS_CORS_ORIGINS 提供精确来源列表，否则用本机正则默认值。"""
    env = os.environ.get("MI_FITNESS_CORS_ORIGINS")
    if env:
        origins = [o.strip() for o in env.split(",") if o.strip()]
        if origins:
            return {"allow_origins": origins}
    return {"allow_origin_regex": default_regex}


def _account_for(api_key: str) -> str:
    digest = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    return f"apikey_{digest}"


def store_api_key_secret(api_key: str, pass_token: str) -> bool:
    """把 API Key 对应的 passToken 存入系统 keyring。返回是否成功。"""
    try:
        import keyring

        keyring.set_password(SERVICE_NAME, _account_for(api_key), pass_token)
        return True
    except Exception:
        return False


def load_api_key_secret(api_key: str) -> str | None:
    try:
        import keyring

        return keyring.get_password(SERVICE_NAME, _account_for(api_key))
    except Exception:
        return None


def delete_api_key_secret(api_key: str) -> None:
    try:
        import keyring

        keyring.delete_password(SERVICE_NAME, _account_for(api_key))
    except Exception:
        pass
