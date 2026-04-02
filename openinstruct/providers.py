from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence
from urllib import error, request

Message = Dict[str, str]


class ProviderError(RuntimeError):
    pass


def _json_request(method: str, url: str, payload: Optional[Dict] = None, timeout: int = 120) -> Dict:
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ProviderError(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc
    except error.URLError as exc:
        raise ProviderError(f"Could not reach {url}: {exc.reason}") from exc
    return json.loads(body) if body else {}


def _pick_model(requested: str, available: Sequence[str]) -> str:
    if not requested:
        if not available:
            raise ProviderError("No models available in provider.")
        return available[0]

    for candidate in available:
        if candidate == requested:
            return candidate
    for candidate in available:
        if candidate.startswith(requested + ":"):
            return candidate
    for candidate in available:
        if candidate.startswith(requested):
            return candidate
    raise ProviderError(f"Model '{requested}' is not available. Found: {', '.join(available) or 'none'}")


@dataclass
class ProviderInfo:
    name: str
    base_url: str
    model: str


class BaseProvider:
    name = "base"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def list_models(self) -> List[str]:
        raise NotImplementedError

    def chat(self, messages: Sequence[Message], model: str, temperature: float = 0.2) -> str:
        raise NotImplementedError

    def resolve_model(self, requested: str) -> str:
        return _pick_model(requested, self.list_models())


class OllamaProvider(BaseProvider):
    name = "ollama"

    def list_models(self) -> List[str]:
        data = _json_request("GET", f"{self.base_url}/api/tags")
        models = [item.get("name", "") for item in data.get("models", [])]
        return [model for model in models if model]

    def chat(self, messages: Sequence[Message], model: str, temperature: float = 0.2) -> str:
        payload = {
            "model": model,
            "messages": list(messages),
            "stream": False,
            "options": {"temperature": temperature},
        }
        data = _json_request("POST", f"{self.base_url}/api/chat", payload)
        message = data.get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("Ollama returned an empty response.")
        return content


class LMStudioProvider(BaseProvider):
    name = "lmstudio"

    def __init__(self, base_url: str):
        normalized = base_url.rstrip("/")
        if not normalized.endswith("/v1"):
            normalized = normalized + "/v1"
        super().__init__(normalized)

    def list_models(self) -> List[str]:
        data = _json_request("GET", f"{self.base_url}/models")
        items = data.get("data", [])
        models = [item.get("id", "") for item in items]
        return [model for model in models if model]

    def chat(self, messages: Sequence[Message], model: str, temperature: float = 0.2) -> str:
        payload = {
            "model": model,
            "messages": list(messages),
            "temperature": temperature,
        }
        data = _json_request("POST", f"{self.base_url}/chat/completions", payload)
        choices = data.get("choices", [])
        if not choices:
            raise ProviderError("LM Studio returned no choices.")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("LM Studio returned an empty response.")
        return content


def available_providers(ollama_base_url: str, lmstudio_base_url: str) -> List[BaseProvider]:
    return [
        OllamaProvider(ollama_base_url),
        LMStudioProvider(lmstudio_base_url),
    ]


def select_provider(
    preference: str,
    model: str,
    ollama_base_url: str,
    lmstudio_base_url: str,
) -> ProviderInfo:
    providers = {provider.name: provider for provider in available_providers(ollama_base_url, lmstudio_base_url)}

    if preference in providers:
        provider = providers[preference]
        resolved_model = provider.resolve_model(model)
        return ProviderInfo(name=provider.name, base_url=provider.base_url, model=resolved_model)

    if preference != "auto":
        raise ProviderError(f"Unknown provider '{preference}'. Use auto, ollama or lmstudio.")

    errors: List[str] = []
    snapshots: List[tuple[BaseProvider, List[str]]] = []
    for provider in providers.values():
        try:
            models = provider.list_models()
        except ProviderError as exc:
            errors.append(f"{provider.name}: {exc}")
            continue
        snapshots.append((provider, models))

    if not snapshots:
        detail = "\n".join(errors) or "No provider responded."
        raise ProviderError(f"No local provider is available.\n{detail}")

    if model:
        for provider, models in snapshots:
            try:
                resolved = _pick_model(model, models)
            except ProviderError:
                continue
            return ProviderInfo(name=provider.name, base_url=provider.base_url, model=resolved)

    provider, models = snapshots[0]
    resolved_model = _pick_model(model, models)
    return ProviderInfo(name=provider.name, base_url=provider.base_url, model=resolved_model)


def instantiate_provider(info: ProviderInfo) -> BaseProvider:
    if info.name == "ollama":
        return OllamaProvider(info.base_url)
    if info.name == "lmstudio":
        return LMStudioProvider(info.base_url)
    raise ProviderError(f"Unsupported provider '{info.name}'.")
