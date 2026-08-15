import json
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from asmr_lrc.errors import TranslationError
from asmr_lrc.providers import OllamaProvider, OpenAICompatibleProvider, ProviderConfig


class ApiHandler(BaseHTTPRequestHandler):
    responses: list[tuple[int, object, float]] = []
    requests: list[dict] = []

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers["Content-Length"])
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.__class__.requests.append(
            {"path": self.path, "body": body, "authorization": self.headers.get("Authorization")}
        )
        status, payload, delay = self.__class__.responses.pop(0)
        if delay:
            time.sleep(delay)
        encoded = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        with suppress(BrokenPipeError, ConnectionResetError):
            self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: object) -> None:
        return


@pytest.fixture
def api_server():
    ApiHandler.responses = []
    ApiHandler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def response_payload(content: str) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def test_provider_infers_translategemma_protocol_from_model() -> None:
    config = ProviderConfig("ollama", "http://127.0.0.1:11434", "translategemma:4b")

    assert config.protocol == "translategemma"


def test_translategemma_request_uses_empty_system_and_zero_temperature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def fake_request(base_url: str, endpoint: str, *, payload: dict, timeout: float) -> dict:
        observed.update({"base_url": base_url, "endpoint": endpoint, "payload": payload})
        return {"response": '{"translations":{},"uncertain_ids":[]}', "eval_count": 3}

    monkeypatch.setattr("asmr_lrc.providers.ollama_request", fake_request)
    provider = OllamaProvider(
        ProviderConfig("ollama", "http://local", "translategemma:4b")
    )
    provider.generate(system="must not be sent", prompt="translate", schema={"type": "object"})

    payload = observed["payload"]
    assert isinstance(payload, dict)
    assert payload["system"] == ""
    assert payload["options"] == {"temperature": 0, "seed": 0}
    assert payload["format"] == {"type": "object"}


def test_openai_provider_uses_strict_schema_and_bearer_key(api_server: str) -> None:
    ApiHandler.responses = [
        (200, response_payload('{"translations":{},"uncertain_ids":[]}'), 0)
    ]
    provider = OpenAICompatibleProvider(
        ProviderConfig("openai", api_server, "remote-model", api_key="secret")
    )

    result = provider.generate(system="system", prompt="prompt", schema={"type": "object"})

    assert result.metrics["strict_schema"] is True
    assert ApiHandler.requests[0]["path"] == "/v1/chat/completions"
    assert ApiHandler.requests[0]["authorization"] == "Bearer secret"
    assert ApiHandler.requests[0]["body"]["response_format"]["type"] == "json_schema"


def test_openai_provider_falls_back_when_json_schema_is_unsupported(api_server: str) -> None:
    ApiHandler.responses = [
        (400, {"error": {"message": "unsupported response_format"}}, 0),
        (200, response_payload('{"translations":{},"uncertain_ids":[]}'), 0),
    ]
    provider = OpenAICompatibleProvider(
        ProviderConfig("openai", api_server, "remote-model", api_key="secret")
    )

    result = provider.generate(system="system", prompt="prompt", schema={"type": "object"})

    assert result.metrics["strict_schema"] is False
    assert len(ApiHandler.requests) == 2
    assert "response_format" not in ApiHandler.requests[1]["body"]


def test_openai_provider_never_includes_key_in_error(api_server: str) -> None:
    ApiHandler.responses = [(401, {"error": {"message": "bad key"}}, 0)]
    provider = OpenAICompatibleProvider(
        ProviderConfig("openai", api_server, "remote-model", api_key="top-secret-value")
    )

    with pytest.raises(TranslationError) as captured:
        provider.generate(system="system", prompt="prompt", schema={"type": "object"})

    assert "top-secret-value" not in str(captured.value)
    assert "HTTP 401" in str(captured.value)


@pytest.mark.parametrize("status", [400, 401, 429, 500])
def test_openai_provider_does_not_fallback_for_unrelated_http_errors(
    api_server: str, status: int
) -> None:
    ApiHandler.responses = [(status, {"error": {"message": "quota or model error"}}, 0)]
    provider = OpenAICompatibleProvider(
        ProviderConfig("openai", api_server, "remote-model", api_key="secret")
    )

    with pytest.raises(TranslationError, match=f"HTTP {status}"):
        provider.generate(system="system", prompt="prompt", schema={"type": "object"})

    assert len(ApiHandler.requests) == 1


def test_openai_provider_reports_timeout_without_key(api_server: str) -> None:
    ApiHandler.responses = [
        (200, response_payload('{"translations":{},"uncertain_ids":[]}'), 0.2)
    ]
    provider = OpenAICompatibleProvider(
        ProviderConfig(
            "openai",
            api_server,
            "remote-model",
            api_key="timeout-secret",
            timeout_seconds=0.02,
        )
    )

    with pytest.raises(TranslationError) as captured:
        provider.generate(system="system", prompt="prompt", schema={"type": "object"})

    assert "timeout-secret" not in str(captured.value)
    assert "请求失败" in str(captured.value)


def test_openai_provider_rejects_malformed_json(api_server: str) -> None:
    ApiHandler.responses = [(200, b"{not-json", 0)]
    provider = OpenAICompatibleProvider(
        ProviderConfig("openai", api_server, "remote-model", api_key="secret")
    )

    with pytest.raises(TranslationError, match="请求失败"):
        provider.generate(system="system", prompt="prompt", schema={"type": "object"})


def test_openai_provider_adds_v1_for_host_base_url(api_server: str) -> None:
    host_base = api_server.removesuffix("/v1")
    ApiHandler.responses = [
        (200, response_payload('{"translations":{},"uncertain_ids":[]}'), 0)
    ]
    provider = OpenAICompatibleProvider(
        ProviderConfig("openai", host_base, "remote-model", api_key="secret")
    )

    provider.generate(system="system", prompt="prompt", schema={"type": "object"})

    assert ApiHandler.requests[0]["path"] == "/v1/chat/completions"
