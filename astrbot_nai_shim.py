"""AstrBot compatibility layer for running astrbot_plugin_nai_image v2.9.0 unmodified inside Hermes."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
import os
from pathlib import Path
import sys
import types
from typing import Any, Iterator

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

nai_logger = logging.getLogger("hermes.nai_image")


def _get_web_req_ctx_var() -> ContextVar[Any]:
    wmod = sys.modules.get("astrbot.api.web")
    if wmod is not None and hasattr(wmod, "_nai_web_ctx_var"):
        return wmod._nai_web_ctx_var
    cv: ContextVar[Any] = ContextVar("nai_web_request", default=None)
    if wmod is not None:
        wmod._nai_web_ctx_var = cv  # type: ignore[attr-defined]
    return cv


class _WebRequestAdapter:
    def __init__(self, fastapi_req: Any, raw_body: bytes | None = None) -> None:
        self._req = fastapi_req
        self._raw_body = raw_body
        self.args = dict(getattr(fastapi_req, "query_params", {}) or {})
        self.headers = getattr(fastapi_req, "headers", {})
        self.method = getattr(fastapi_req, "method", "GET")

    async def json(self, default: Any = None) -> Any:
        try:
            body = self._raw_body if self._raw_body is not None else await self._req.body()
            if not body:
                return default
            return json.loads(body.decode("utf-8", errors="replace"))
        except Exception:
            if default is not None:
                return default
            raise


class _WebRequestProxy:
    def __getattr__(self, name: str) -> Any:
        req = _get_web_req_ctx_var().get()
        if req is None:
            if name == "args":
                return {}
            raise RuntimeError("No active HTTP request in astrbot.api.web.request proxy")
        return getattr(req, name)


@contextmanager
def bind_nai_web_request(fastapi_req: Any, raw_body: bytes | None = None) -> Iterator[None]:
    cv = _get_web_req_ctx_var()
    token = cv.set(_WebRequestAdapter(fastapi_req, raw_body=raw_body))
    try:
        yield
    finally:
        cv.reset(token)


def json_response(data: Any = None, *, status_code: int = 200, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        jsonable_encoder({} if data is None else data),
        status_code=status_code,
        headers=headers,
    )


def error_response(
    message: str,
    *,
    status_code: int = 400,
    data: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return json_response(
        {"status": "error", "message": message, "data": data},
        status_code=status_code,
        headers=headers,
    )


class AstrBotConfig(dict):
    """Persistent dict with ``save_config()`` method expected by NaiImagePlugin."""

    def __init__(
        self,
        config_path: str | Path | None = None,
        initial: dict[str, Any] | None = None,
        schema: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> None:
        p = Path(config_path) if config_path else None
        data: dict[str, Any] = {}
        if isinstance(schema, dict):
            for k, meta in schema.items():
                if isinstance(meta, dict) and "default" in meta:
                    data[k] = meta["default"]
        if initial is not None:
            data.update(initial)
        elif p is not None and p.is_file():
            try:
                loaded = json.loads(p.read_text(encoding="utf-8-sig"))
                if isinstance(loaded, dict):
                    data.update(loaded)
            except Exception:
                pass
        super().__init__(data)
        self.config_path = p
        self.schema = schema or {}

    def save_config(self) -> None:
        if self.config_path is None:
            return
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(dict(self), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.config_path)


class Plain:
    def __init__(self, text: str = "") -> None:
        self.text = text


class Image:
    def __init__(self, data_bytes: bytes | None = None, path: str | None = None) -> None:
        self.data_bytes = data_bytes
        self.path = path

    @classmethod
    def fromBytes(cls, data: bytes) -> "Image":
        return cls(data_bytes=data)

    @classmethod
    def fromFileSystem(cls, path: str) -> "Image":
        return cls(path=str(path))


class MessageChain:
    def __init__(self, chain: list[Any] | None = None) -> None:
        self.chain = list(chain or [])


class AstrMessageEvent:
    """Synthetic event that captures generated images sent via ``await event.send(MessageChain(...))``."""

    def __init__(self, session_id: str = "default", sender_id: str = "user", message_str: str = "") -> None:
        self.unified_msg_origin = session_id
        self.session_id = session_id
        self.message_str = message_str
        self._sender_id = sender_id
        self._extras: dict[str, Any] = {}
        self.sent_images: list[bytes] = []
        self.sent_texts: list[str] = []

    def get_sender_id(self) -> str:
        return self._sender_id

    def get_extra(self, key: str, default: Any = None) -> Any:
        return self._extras.get(key, default)

    def set_extra(self, key: str, value: Any) -> None:
        self._extras[key] = value

    def plain_result(self, text: str) -> str:
        self.sent_texts.append(str(text))
        return str(text)

    def chain_result(self, chain: list[Any]) -> list[Any]:
        for item in chain:
            if isinstance(item, Image) and item.data_bytes:
                self.sent_images.append(item.data_bytes)
            elif isinstance(item, Plain) and item.text:
                self.sent_texts.append(item.text)
        return chain

    async def send(self, message_chain: MessageChain) -> None:
        for item in getattr(message_chain, "chain", []) or []:
            if isinstance(item, Image):
                if item.data_bytes:
                    self.sent_images.append(item.data_bytes)
                elif item.path and os.path.isfile(item.path):
                    self.sent_images.append(Path(item.path).read_bytes())
            elif isinstance(item, Plain) and item.text:
                self.sent_texts.append(item.text)


class _FilterNamespace:
    @staticmethod
    def command(*_args: Any, **_kwargs: Any):
        def _dec(fn: Any) -> Any:
            return fn
        return _dec

    @staticmethod
    def llm_tool(*_args: Any, **_kwargs: Any):
        def _dec(fn: Any) -> Any:
            return fn
        return _dec

    @staticmethod
    def on_astrbot_loaded(*_args: Any, **_kwargs: Any):
        def _dec(fn: Any) -> Any:
            return fn
        return _dec

    class PermissionType:
        ADMIN = "admin"
        MEMBER = "member"

    @staticmethod
    def permission_type(*_args: Any, **_kwargs: Any):
        def _dec(fn: Any) -> Any:
            return fn
        return _dec


class Star:
    def __init__(self, context: Any = None, *_args: Any, **_kwargs: Any) -> None:
        self.context = context


class StarTools:
    @staticmethod
    def get_data_dir(plugin_name: str = "astrbot_plugin_nai_image") -> Path:
        try:
            from hermes_constants import get_hermes_home

            base = Path(get_hermes_home()).resolve()
        except Exception:
            base = Path("/root/hermes")
        d = base / "nai_image"
        d.mkdir(parents=True, exist_ok=True)
        return d


def register(*_args: Any, **_kwargs: Any):
    def _dec(cls: Any) -> Any:
        return cls
    return _dec


def get_astrbot_config_path() -> str:
    return str(StarTools.get_data_dir())


class NaiHostContext:
    """Minimal AstrBot Context for NaiImagePlugin."""

    def __init__(self) -> None:
        self.web_routes: dict[str, tuple[Any, list[str], str]] = {}

    def register_web_api(self, route: str, handler: Any, methods: list[str], desc: str = "") -> None:
        self.web_routes[route] = (handler, list(methods), desc)

    def get_all_providers(self) -> list[Any]:
        return []

    def get_using_provider(self, umo: str | None = None) -> Any:
        return None

    def get_provider_by_id(self, provider_id: str) -> Any:
        return None

    def get_llm_tool_manager(self) -> Any:
        return types.SimpleNamespace(get_func=lambda _name: None)


def install_nai_astrbot_shim() -> None:
    """Install or augment ``sys.modules['astrbot.*']`` without clobbering existing shims (like LivingMemory)."""

    def _pkg(name: str) -> types.ModuleType:
        mod = sys.modules.get(name)
        if mod is None:
            mod = types.ModuleType(name)
            mod.__path__ = []  # type: ignore[attr-defined]
            sys.modules[name] = mod
        return mod

    astrbot_root = _pkg("astrbot")
    if not hasattr(astrbot_root, "logger"):
        astrbot_root.logger = nai_logger  # type: ignore[attr-defined]

    api_mod = _pkg("astrbot.api")
    if not hasattr(api_mod, "logger"):
        api_mod.logger = nai_logger  # type: ignore[attr-defined]

    event_mod = _pkg("astrbot.api.event")
    if not hasattr(event_mod, "AstrMessageEvent"):
        event_mod.AstrMessageEvent = AstrMessageEvent  # type: ignore[attr-defined]
    event_mod.MessageChain = MessageChain  # type: ignore[attr-defined]
    if not hasattr(event_mod, "filter") or not hasattr(getattr(event_mod, "filter", None), "llm_tool"):
        event_mod.filter = _FilterNamespace  # type: ignore[attr-defined]

    mc_mod = _pkg("astrbot.api.message_components")
    mc_mod.Image = Image  # type: ignore[attr-defined]
    mc_mod.Plain = Plain  # type: ignore[attr-defined]

    star_mod = _pkg("astrbot.api.star")
    if not hasattr(star_mod, "Context"):
        star_mod.Context = NaiHostContext  # type: ignore[attr-defined]
    star_mod.Star = Star  # type: ignore[attr-defined]
    star_mod.StarTools = StarTools  # type: ignore[attr-defined]
    star_mod.register = register  # type: ignore[attr-defined]

    web_mod = _pkg("astrbot.api.web")
    if not hasattr(web_mod, "_nai_web_ctx_var"):
        web_mod._nai_web_ctx_var = ContextVar("nai_web_request", default=None)  # type: ignore[attr-defined]
    web_mod.request = _WebRequestProxy()  # type: ignore[attr-defined]
    web_mod.json_response = json_response  # type: ignore[attr-defined]
    web_mod.error_response = error_response  # type: ignore[attr-defined]

    _pkg("astrbot.core")
    _pkg("astrbot.core.config")
    cfg_mod = _pkg("astrbot.core.config.astrbot_config")
    cfg_mod.AstrBotConfig = AstrBotConfig  # type: ignore[attr-defined]

    _pkg("astrbot.core.utils")
    path_mod = _pkg("astrbot.core.utils.astrbot_path")
    path_mod.get_astrbot_config_path = get_astrbot_config_path  # type: ignore[attr-defined]
