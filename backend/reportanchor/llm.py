"""단일 AI 연결 모듈 (MVP: Ollama `/api/chat`).

- 모든 LLM 호출은 이 모듈을 거치며, 호출 직전에 허용 주소 목록(Allowlist)을 다시 검사한다.
- 구조화 출력(`format`)은 로컬 Ollama에서만 보내고, Cloud는 프롬프트 + 엄격한 JSON 검사로 처리한다
  (`origin/JM:rag/llm/ollama_client.py`와 같은 동작).
- API 키는 cloud 프로필에서만 `Authorization` 헤더로 보내고, 로그·아티팩트·응답에 남기지 않는다.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from .config import CLOUD_BASE_URL, CLOUD_DEFAULT_MODEL, DEFAULT_ALLOWED_OLLAMA_HOSTS, LOCAL_BASE_URL, Settings
from .store import Store


class ModelError(Exception):
    """`code`는 내부 분류, `message`는 사용자에게 보여도 안전한 문장."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_origin(url: str) -> str:
    """`scheme://host[:port]`로 정규화한다. 경로·쿼리·계정 정보가 있으면 거부한다."""
    p = urlsplit(url.strip())
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("http(s) 주소가 아닙니다.")
    if p.username or p.password or p.query or p.fragment or p.path not in ("", "/"):
        raise ValueError("주소에는 scheme://host[:port]만 쓸 수 있습니다.")
    host = p.hostname.lower()
    port = p.port  # 잘못된 포트면 ValueError
    if ":" in host:
        host = f"[{host}]"
    default_port = 80 if p.scheme == "http" else 443
    return f"{p.scheme}://{host}" if port in (None, default_port) else f"{p.scheme}://{host}:{port}"


@dataclass
class ModelConfig:
    provider: str  # "local" | "cloud"
    base_url: str
    model: str


class ModelManager:
    def __init__(self, settings: Settings, store: Store, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.store = store
        self.transport = transport
        self._lock = threading.Lock()
        self._cfg = ModelConfig("local", LOCAL_BASE_URL, "")
        allowed = set()
        for h in (*DEFAULT_ALLOWED_OLLAMA_HOSTS, *settings.extra_allowed_ollama_hosts):
            try:
                allowed.add(normalize_origin(h))
            except ValueError:
                continue
        self.allowed = frozenset(allowed)

    # ---- 설정 ----
    def is_allowed(self, base_url: str) -> bool:
        try:
            return normalize_origin(base_url) in self.allowed
        except ValueError:
            return False

    def get(self) -> ModelConfig:
        with self._lock:
            return ModelConfig(**asdict(self._cfg))

    def enabled(self, cfg: ModelConfig | None = None) -> bool:
        cfg = cfg or self.get()
        if not cfg.model or not self.is_allowed(cfg.base_url):
            return False
        return cfg.provider == "local" or bool(self.settings.api_key)

    def describe(self) -> dict[str, Any]:
        cfg = self.get()
        return {
            "provider": cfg.provider,
            "base_url": cfg.base_url,
            "model": cfg.model,
            "enabled": self.enabled(cfg),
            "api_key_configured": bool(self.settings.api_key),
        }

    def set(self, provider: str, model: str, base_url: str | None) -> ModelConfig:
        if provider not in ("local", "cloud"):
            raise ValueError("provider는 local 또는 cloud여야 합니다.")
        url = base_url or (LOCAL_BASE_URL if provider == "local" else CLOUD_BASE_URL)
        origin = normalize_origin(url)  # 형식 오류는 ValueError
        if origin not in self.allowed:
            raise PermissionError("허용 주소 목록(Allowlist)에 없는 AI 서버 주소입니다.")
        cfg = ModelConfig(provider, origin, model.strip())
        with self._lock:
            self._cfg = cfg
        self.store.put_setting("model", asdict(cfg))
        return cfg

    def initialize(self) -> None:
        saved = self.store.get_setting("model")
        if isinstance(saved, dict):
            try:
                cfg = ModelConfig(str(saved["provider"]), normalize_origin(str(saved["base_url"])), str(saved["model"]))
                if cfg.provider in ("local", "cloud") and cfg.base_url in self.allowed:
                    with self._lock:
                        self._cfg = cfg
                    return
            except (KeyError, ValueError):
                pass
        if not self.settings.autodetect_model:
            return
        local_model = self._probe_local()
        if local_model:
            cfg = ModelConfig("local", LOCAL_BASE_URL, local_model)
        elif self.settings.api_key:
            cfg = ModelConfig("cloud", CLOUD_BASE_URL, CLOUD_DEFAULT_MODEL)
        else:
            cfg = ModelConfig("local", LOCAL_BASE_URL, self.settings.local_model or "")
        with self._lock:
            self._cfg = cfg

    def _probe_local(self) -> str | None:
        try:
            with httpx.Client(transport=self.transport, timeout=1.5, trust_env=False) as c:
                r = c.get(LOCAL_BASE_URL + "/api/tags")
            if r.status_code != 200:
                return None
            names = [m.get("name") for m in r.json().get("models", []) if isinstance(m, dict) and m.get("name")]
        except (httpx.HTTPError, ValueError):
            return None
        if self.settings.local_model:
            return self.settings.local_model if (not names or self.settings.local_model in names) else None
        return names[0] if names else None

    # ---- 호출 ----
    def chat(self, messages: list[dict[str, str]], *, json_mode: bool = True, cfg: ModelConfig | None = None) -> str:
        cfg = cfg or self.get()
        if not cfg.model:
            raise ModelError("disabled", "AI 모델이 설정되지 않았습니다.")
        try:
            origin = normalize_origin(cfg.base_url)
        except ValueError:
            raise ModelError("blocked", "허용되지 않은 AI 서버 주소입니다.") from None
        if origin not in self.allowed:
            raise ModelError("blocked", "허용되지 않은 AI 서버 주소입니다.")
        payload: dict[str, Any] = {"model": cfg.model, "messages": messages, "stream": False}
        headers = {"Content-Type": "application/json"}
        if cfg.provider == "local":
            payload["options"] = {"temperature": 0}
            if json_mode:
                payload["format"] = "json"
        else:
            if not self.settings.api_key:
                raise ModelError("disabled", "클라우드 모드에는 API 키가 필요합니다.")
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        try:
            with httpx.Client(
                transport=self.transport,
                timeout=self.settings.llm_timeout,
                follow_redirects=False,  # 허용 주소 밖으로의 리다이렉트 차단
                trust_env=cfg.provider == "cloud",
            ) as c:
                r = c.post(origin + "/api/chat", json=payload, headers=headers)
        except httpx.TimeoutException:
            raise ModelError("timeout", "AI 서버 응답 시간이 초과되었습니다.") from None
        except httpx.HTTPError:
            raise ModelError("unreachable", "AI 서버에 연결하지 못했습니다.") from None
        if r.status_code == 429:
            raise ModelError("rate_limited", "AI 서버 요청이 많습니다. 잠시 후 다시 시도해 주세요.")
        if r.status_code in (401, 403):
            raise ModelError("auth", "AI 서버 인증에 실패했습니다.")
        if r.status_code >= 400:
            raise ModelError("http_error", "AI 서버가 요청을 처리하지 못했습니다.")
        try:
            content = r.json()["message"]["content"]
        except (ValueError, KeyError, TypeError):
            raise ModelError("bad_response", "AI 서버 응답 형식이 올바르지 않습니다.") from None
        if not isinstance(content, str) or not content.strip():
            raise ModelError("bad_response", "AI 서버가 빈 응답을 반환했습니다.")
        return content

    def chat_json(self, messages: list[dict[str, str]], *, cfg: ModelConfig | None = None) -> dict[str, Any]:
        return parse_json_object(self.chat(messages, json_mode=True, cfg=cfg))


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_json_object(text: str) -> dict[str, Any]:
    """코드 블록 표시나 앞뒤 설명이 붙어도 첫 JSON 객체만 엄격하게 읽는다."""
    s = _FENCE_RE.sub("", text.strip())
    start = s.find("{")
    if start < 0:
        raise ModelError("bad_json", "AI 응답에서 JSON을 찾지 못했습니다.")
    try:
        obj, _ = json.JSONDecoder().raw_decode(s[start:])
    except json.JSONDecodeError:
        raise ModelError("bad_json", "AI 응답 JSON을 해석하지 못했습니다.") from None
    if not isinstance(obj, dict):
        raise ModelError("bad_json", "AI 응답이 JSON 객체가 아닙니다.")
    return obj
