from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

from .environment import ollama_models, ollama_request
from .errors import TranslationError

PROVIDER_PROTOCOLS = frozenset({"chat-json", "translategemma"})


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    kind: str
    base_url: str
    model: str
    api_key: str | None = field(default=None, repr=False, compare=False)
    strict_schema: bool = True
    timeout_seconds: float = 600
    keep_alive: str = "5m"
    protocol: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in {"ollama", "openai"}:
            raise ValueError(f"不支持的翻译提供方: {self.kind}")
        if not self.base_url.strip():
            raise ValueError("翻译提供方 base_url 不能为空")
        if not self.model.strip():
            raise ValueError("翻译提供方 model 不能为空")
        protocol = self.protocol
        if protocol is None:
            protocol = (
                "translategemma"
                if self.model.casefold().split(":", 1)[0] == "translategemma"
                else "chat-json"
            )
            object.__setattr__(self, "protocol", protocol)
        if protocol not in PROVIDER_PROTOCOLS:
            raise ValueError(f"不支持的翻译协议: {protocol}")
        if protocol == "translategemma" and self.kind != "ollama":
            raise ValueError("translategemma 协议仅支持 Ollama")

    def cache_identity(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "base_url": self.base_url.rstrip("/"),
            "model": self.model,
            "strict_schema": self.strict_schema,
            "protocol": self.protocol,
        }


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    text: str
    metrics: dict[str, object]


class TranslationProvider(Protocol):
    config: ProviderConfig

    def check(self) -> None: ...

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        schema: dict[str, object],
    ) -> ProviderResponse: ...

    def unload(self) -> None: ...


class _HttpStatusError(TranslationError):
    def __init__(self, code: int, detail: str) -> None:
        self.code = code
        self.detail = detail
        suffix = f": {detail}" if detail else ""
        super().__init__(f"外部 API HTTP {code}{suffix}")


class OllamaProvider:
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    def check(self) -> None:
        try:
            models = ollama_models(self.config.base_url)
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
            raise TranslationError(
                f"无法连接 Ollama 服务 {self.config.base_url}: {exc}"
            ) from exc
        if self.config.model not in models:
            raise TranslationError(f"Ollama 模型未安装: {self.config.model}")

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        schema: dict[str, object],
    ) -> ProviderResponse:
        response = ollama_request(
            self.config.base_url,
            "/api/generate",
            payload={
                "model": self.config.model,
                # TranslateGemma is trained for a single user message.  The
                # adapter renders its complete prompt in ``prompt`` and must
                # not receive the generic chat system message.
                "system": "" if self.config.protocol == "translategemma" else system,
                "prompt": prompt,
                "stream": False,
                "think": False,
                "format": schema,
                "keep_alive": self.config.keep_alive,
                "options": {"temperature": 0, "seed": 0},
            },
            timeout=self.config.timeout_seconds,
        )
        raw = response.get("response")
        if not isinstance(raw, str):
            raise TranslationError("Ollama 响应缺少 response 字符串")
        return ProviderResponse(
            raw,
            {
                "provider": "ollama",
                "model": self.config.model,
                "strict_schema": True,
                "total_duration_ns": response.get("total_duration"),
                "eval_count": response.get("eval_count"),
                "prompt_eval_count": response.get("prompt_eval_count"),
            },
        )

    def unload(self) -> None:
        try:
            ollama_request(
                self.config.base_url,
                "/api/generate",
                payload={"model": self.config.model, "keep_alive": 0},
                timeout=30,
            )
        except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
            raise TranslationError(
                f"无法卸载 Ollama 模型 {self.config.model}: {exc}"
            ) from exc


class OpenAICompatibleProvider:
    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    @property
    def endpoint(self) -> str:
        base = self.config.base_url.rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        if base.endswith("/v1"):
            return f"{base}/chat/completions"
        return f"{base}/v1/chat/completions"

    def check(self) -> None:
        if not self.config.api_key:
            raise TranslationError("外部 OpenAI 兼容 API 缺少 API Key")

    def _request(self, payload: dict[str, object]) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.config.timeout_seconds
            ) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                body = exc.read().decode("utf-8", errors="replace")
                parsed = json.loads(body)
                if isinstance(parsed, dict):
                    error = parsed.get("error")
                    if isinstance(error, dict):
                        detail = str(error.get("message", ""))
                    elif error is not None:
                        detail = str(error)
                if not detail:
                    detail = body.strip()
            except (OSError, ValueError, json.JSONDecodeError):
                detail = ""
            if self.config.api_key:
                detail = detail.replace(self.config.api_key, "<redacted>")
            raise _HttpStatusError(exc.code, detail[:500]) from exc
        except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError) as exc:
            raise TranslationError(f"外部 API 请求失败: {exc}") from exc
        if not isinstance(result, dict):
            raise TranslationError("外部 API 返回的根节点不是 JSON 对象")
        return result

    @staticmethod
    def _schema_is_unsupported(error: _HttpStatusError) -> bool:
        if error.code not in {400, 404, 422}:
            return False
        detail = error.detail.casefold()
        schema_markers = (
            "response_format",
            "json_schema",
            "json schema",
            "structured output",
        )
        unsupported_markers = (
            "unsupported",
            "not support",
            "unknown",
            "unrecognized",
            "invalid",
            "not found",
        )
        return any(marker in detail for marker in schema_markers) and any(
            marker in detail for marker in unsupported_markers
        )

    @staticmethod
    def _content(result: dict[str, Any]) -> str:
        try:
            content = result["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise TranslationError("外部 API 响应缺少 choices[0].message.content") from exc
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") in {"text", "output_text"}
            ]
            if parts:
                return "".join(parts)
        raise TranslationError("外部 API 响应 content 不是文本")

    def generate(
        self,
        *,
        system: str,
        prompt: str,
        schema: dict[str, object],
    ) -> ProviderResponse:
        self.check()
        base_payload: dict[str, object] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
            "stream": False,
        }
        strict_used = self.config.strict_schema
        if strict_used:
            payload = {
                **base_payload,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": "asmr_translation",
                        "strict": True,
                        "schema": schema,
                    },
                },
            }
            try:
                result = self._request(payload)
            except _HttpStatusError as exc:
                if not self._schema_is_unsupported(exc):
                    raise
                strict_used = False
                result = self._request(base_payload)
        else:
            result = self._request(base_payload)
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        return ProviderResponse(
            self._content(result),
            {
                "provider": "openai",
                "model": self.config.model,
                "strict_schema": strict_used,
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
            },
        )

    def unload(self) -> None:
        return


def create_provider(config: ProviderConfig) -> TranslationProvider:
    if config.kind == "ollama":
        return OllamaProvider(config)
    return OpenAICompatibleProvider(config)
