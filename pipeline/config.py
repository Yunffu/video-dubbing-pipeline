"""全局配置：全部走环境变量，不硬编码任何密钥。

环境变量一览（可选，均有默认值）：
    TRANSLATE_PROVIDER        deepseek | openai_compatible | offline | auto（默认 auto）
    DEEPSEEK_API_KEY          DeepSeek 密钥
    DEEPSEEK_BASE_URL         默认 https://api.deepseek.com/v1
    DEEPSEEK_MODEL            默认 deepseek-chat
    OPENAI_API_KEY            任意 OpenAI 兼容端点的密钥
    OPENAI_BASE_URL           任意 OpenAI 兼容端点，形如 https://host/v1
    OPENAI_MODEL              端点上的模型名
    TRANSLATE_TIMEOUT         单次请求超时秒数（默认 30）
    TRANSLATE_MAX_RETRIES     失败重试次数（默认 2）
    TRANSLATE_GLOSSARY        术语表 JSON 路径（离线降级与 prompt 注入都用）
    TRANSLATE_OFFLINE_STRICT  设为 1 时离线降级只做术语替换，不透传原句
"""
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """读项目根目录的 .env（不入库），让 Windows 下配密钥不用改系统环境变量。"""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip("'\"")
        if k and k not in os.environ:
            os.environ[k] = v


_load_dotenv()


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name) or default)
    except ValueError:
        return default


@dataclass
class TranslateConfig:
    provider: str = field(default_factory=lambda: _env("TRANSLATE_PROVIDER", "auto"))
    deepseek_api_key: str = field(default_factory=lambda: _env("DEEPSEEK_API_KEY"))
    deepseek_base_url: str = field(
        default_factory=lambda: _env("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1"))
    deepseek_model: str = field(default_factory=lambda: _env("DEEPSEEK_MODEL", "deepseek-chat"))
    openai_api_key: str = field(default_factory=lambda: _env("OPENAI_API_KEY"))
    openai_base_url: str = field(default_factory=lambda: _env("OPENAI_BASE_URL"))
    openai_model: str = field(default_factory=lambda: _env("OPENAI_MODEL"))
    timeout: int = field(default_factory=lambda: _env_int("TRANSLATE_TIMEOUT", 30))
    max_retries: int = field(default_factory=lambda: _env_int("TRANSLATE_MAX_RETRIES", 2))
    glossary_path: str = field(default_factory=lambda: _env("TRANSLATE_GLOSSARY"))
    offline_strict: bool = field(
        default_factory=lambda: _env("TRANSLATE_OFFLINE_STRICT") == "1")

    def glossary(self) -> dict:
        if not self.glossary_path:
            return {}
        p = Path(self.glossary_path)
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def describe(self) -> dict:
        """给日志/报告用的脱敏视图（不泄漏密钥）。"""
        return {
            "provider": self.provider,
            "deepseek": {"configured": bool(self.deepseek_api_key),
                         "base_url": self.deepseek_base_url,
                         "model": self.deepseek_model},
            "openai_compatible": {"configured": bool(self.openai_api_key),
                                  "base_url": self.openai_base_url,
                                  "model": self.openai_model},
            "timeout": self.timeout,
            "max_retries": self.max_retries,
            "glossary_terms": len(self.glossary()),
            "offline_strict": self.offline_strict,
        }
