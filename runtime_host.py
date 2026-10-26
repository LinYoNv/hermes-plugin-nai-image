"""Dedicated asyncio loop & runtime host for NovelAI Image Generation (v2.9.0) inside Hermes."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
import contextlib
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any
import uuid

from .astrbot_nai_shim import (
    AstrBotConfig,
    AstrMessageEvent,
    NaiHostContext,
    install_nai_astrbot_shim,
)

install_nai_astrbot_shim()

from .nai_pkg.command_args import SAMPLERS, normalize_image_size, normalize_image_style
from .nai_pkg.grimoire_service import get_grimoire_db_path, search_grimoire
from .nai_pkg.main import (
    IMAGE_SIZES,
    IMAGE_STYLES,
    DEFAULT_NEGATIVE,
    NAIGenerateImagePlugin as NaiImagePlugin,
    OPENAI_COMPATIBLE_MODELS,
    OPENAI_TOOL_SIZES,
)
DIRECT_MODELS = ('nai-diffusion-4-5-full', 'nai-diffusion-5-full')
from .nai_pkg.prompt_processing import normalize_prompt

logger = logging.getLogger("hermes.nai_image")

_PENDING_LOCK = threading.Lock()
_PENDING_MEDIA: OrderedDict[str, list[tuple[str, float]]] = OrderedDict()


def record_pending_media(session_id: str, path: Path) -> None:
    if not session_id:
        return
    now = time.monotonic()
    with _PENDING_LOCK:
        for k in list(_PENDING_MEDIA.keys()):
            _PENDING_MEDIA[k] = [it for it in _PENDING_MEDIA[k] if now - it[1] < 1800]
            if not _PENDING_MEDIA[k]:
                del _PENDING_MEDIA[k]
        _PENDING_MEDIA.setdefault(session_id, []).append((str(path), now))
        _PENDING_MEDIA.move_to_end(session_id)
        while len(_PENDING_MEDIA) > 256:
            _PENDING_MEDIA.popitem(last=False)


def take_pending_media(session_id: str) -> list[str]:
    if not session_id:
        return []
    now = time.monotonic()
    with _PENDING_LOCK:
        items = _PENDING_MEDIA.pop(session_id, [])
        return [p for p, ts in items if now - ts < 1800]


def on_transform_llm_output(response_text: str = "", session_id: str = "", turn_id: str = "", **kwargs: Any) -> str | None:
    paths = take_pending_media(session_id)
    if not paths:
        return None
    additions: list[str] = []
    for p_str in paths:
        p = Path(p_str)
        if not p.is_file():
            continue
        tag = f"MEDIA:{p}"
        if tag not in (response_text or "") and tag not in additions:
            additions.append(tag)
    if not additions:
        return None
    return "\n".join(part for part in ((response_text or "").rstrip(), "\n".join(additions)) if part)


def _read_scoped_secret(key: str) -> str:
    try:
        from agent.secret_scope import get_secret

        v = get_secret(key)
        if v:
            return str(v).strip()
    except Exception:
        pass
    try:
        from hermes_cli.config import load_env

        v = (load_env() or {}).get(key)
        if v:
            return str(v).strip()
    except Exception:
        pass
    return str(os.environ.get(key) or "").strip()


def _generated_images_dir(hermes_home: Path) -> Path:
    d = hermes_home / "cache" / "generated" / "images"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sniff_ext(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    return "png"


class NaiImageHostRuntime:
    """Owns the NaiImagePlugin instance and its dedicated asyncio loop for one ``HERMES_HOME``."""

    def __init__(self, hermes_home: Path) -> None:
        self.hermes_home = hermes_home.resolve()
        self.data_dir = self.hermes_home / "nai_image"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.config_path = self.data_dir / "astrbot_plugin_nai_image_config.json"

        self.loop = asyncio.new_event_loop()
        self._loop_ready = threading.Event()
        from agent.memory_provider import spawn_context_thread

        self._thread = spawn_context_thread(
            self._run_loop,
            name=f"nai-image-loop-{self.hermes_home.name}",
            daemon=True,
        )
        self._thread.start()
        self._loop_ready.wait(timeout=5.0)

        self.context = NaiHostContext()
        self.config: AstrBotConfig | None = None
        self.plugin: NaiImagePlugin | None = None
        self._booted = False
        self._boot_lock: asyncio.Lock | None = None

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self._loop_ready.set()
        self.loop.run_forever()

    def run_sync(self, coro: Any, timeout: float = 240.0) -> Any:
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result(timeout=timeout)

    async def run_from_other_loop(self, coro: Any, timeout: float = 240.0) -> Any:
        if asyncio.get_running_loop() is self.loop:
            return await coro
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return await asyncio.wait_for(asyncio.wrap_future(fut), timeout=timeout)

    def _load_or_init_config(self) -> AstrBotConfig:
        initial: dict[str, Any] = {}
        if self.config_path.is_file():
            with contextlib.suppress(Exception):
                loaded = json.loads(self.config_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    initial = loaded

        # Defaults tailored for Hermes Agent
        initial.setdefault("call_mode", "direct")
        initial.setdefault("base_url", "https://nai.sta1n.cn")
        initial.setdefault("model", "nai-diffusion-4-5-full")
        initial.setdefault("openai_api_model", "nai-diffusion-5-full")
        initial.setdefault("image_size", "竖图")
        initial.setdefault("image_style", "vertical")
        initial.setdefault("image_count", 1)
        initial.setdefault("steps", 24)
        initial.setdefault("scale", 6.0)
        initial.setdefault("cfg", 1.0)
        initial.setdefault("sampler", "k_dpmpp_2m_sde")
        initial.setdefault("enable_artist_slots", True)
        initial.setdefault("enable_llm_tool", True)
        initial.setdefault("enable_tool_style", True)
        initial.setdefault("enable_character_blocks", True)
        initial.setdefault("enable_grimoire", True)
        initial.setdefault("save_image_history", True)
        initial.setdefault("image_history_limit", 200)
        initial.setdefault("enable_proxy", False)
        initial.setdefault("bypass_system_proxy", True)

        # Overlay secrets from Hermes .env if set
        tok = _read_scoped_secret("NAI_IMAGE_TOKEN")
        if tok:
            initial["image_gen_key"] = tok
        oa_key = _read_scoped_secret("NAI_OPENAI_API_KEY")
        if oa_key:
            initial["openai_api_key"] = oa_key

        cfg = AstrBotConfig(self.config_path, initial)
        if not self.config_path.is_file():
            cfg.save_config()
        return cfg

    def sync_env_secrets_into_plugin(self) -> None:
        if self.config is None or self.plugin is None:
            return
        tok = _read_scoped_secret("NAI_IMAGE_TOKEN")
        if tok:
            self.config["image_gen_key"] = tok
            self.plugin.image_gen_key = tok
        oa_key = _read_scoped_secret("NAI_OPENAI_API_KEY")
        if oa_key:
            self.config["openai_api_key"] = oa_key
            self.plugin.openai_api_key = oa_key

    async def ensure_initialized(self) -> NaiImagePlugin:
        if self._boot_lock is None:
            self._boot_lock = asyncio.Lock()
        async with self._boot_lock:
            if self._booted and self.plugin is not None:
                self.sync_env_secrets_into_plugin()
                return self.plugin

            self.config = self._load_or_init_config()
            self.plugin = NaiImagePlugin(self.context, self.config)
            await self.plugin.initialize()
            self._booted = True
            logger.info("NAI Image runtime initialized at %s", self.data_dir)
            return self.plugin

    async def get_settings_dict(self) -> dict[str, Any]:
        plugin = await self.ensure_initialized()
        assert self.config is not None
        g_path = await asyncio.to_thread(get_grimoire_db_path)
        g_status = {'available': g_path is not None, 'path': str(g_path) if g_path else ''}
        slots = plugin._load_artist_slots()
        return {
            "ok": True,
            "call_mode": plugin.call_mode,
            "base_url": plugin.base_url,
            "has_token": bool(plugin.image_gen_key),
            "openai_api_base_url": plugin.openai_api_base_url,
            "has_openai_key": bool(plugin.openai_api_key),
            "model": plugin.model,
            "openai_api_model": plugin.openai_api_model,
            "image_size": plugin.image_size,
            "image_style": plugin.image_style,
            "custom_artists": plugin.custom_artists,
            "steps": plugin.steps,
            "scale": plugin.scale,
            "cfg": plugin.cfg_value,
            "sampler": plugin.sampler,
            "negative": plugin.negative,
            "default_negative": DEFAULT_NEGATIVE,
            "enable_artist_slots": plugin.enable_artist_slots,
            "active_artist_slot": plugin.active_artist_slot,
            "artist_slots": slots,
            "enable_character_blocks": plugin.enable_character_blocks,
            "enable_grimoire": plugin.enable_grimoire,
            "grimoire_status": g_status,
            "direct_models": list(DIRECT_MODELS),
            "openai_models": list(OPENAI_COMPATIBLE_MODELS),
            "samplers": ["k_dpmpp_2m_sde", "k_dpmpp_2m", "k_dpmpp_sde", "k_dpmpp_2s_ancestral", "k_euler_ancestral", "k_euler"],
            "image_sizes": list(IMAGE_SIZES.keys()),
            "image_styles": [{"key": k, "label": v} for k, v in IMAGE_STYLES.items()],
        }

    async def save_settings_dict(self, body: dict[str, Any]) -> dict[str, Any]:
        plugin = await self.ensure_initialized()
        assert self.config is not None

        from hermes_cli.config import save_env_value

        if isinstance(body.get("token"), str) and body["token"].strip():
            tok = body["token"].strip()
            save_env_value("NAI_IMAGE_TOKEN", tok)
            self.config["image_gen_key"] = tok
            plugin.image_gen_key = tok

        if isinstance(body.get("openai_api_key"), str) and body["openai_api_key"].strip():
            oak = body["openai_api_key"].strip()
            save_env_value("NAI_OPENAI_API_KEY", oak)
            self.config["openai_api_key"] = oak
            plugin.openai_api_key = oak

        field_map: list[tuple[str, str, Any]] = [
            ("call_mode", "call_mode", str),
            ("base_url", "base_url", str),
            ("openai_api_base_url", "openai_api_base_url", str),
            ("model", "model", str),
            ("openai_api_model", "openai_api_model", str),
            ("image_size", "image_size", str),
            ("image_style", "image_style", str),
            ("custom_artists", "custom_artists", str),
            ("steps", "steps", int),
            ("scale", "scale", float),
            ("cfg", "cfg_value", float),
            ("sampler", "sampler", str),
            ("negative", "negative", str),
            ("enable_artist_slots", "enable_artist_slots", bool),
            ("active_artist_slot", "active_artist_slot", str),
            ("enable_character_blocks", "enable_character_blocks", bool),
            ("enable_grimoire", "enable_grimoire", bool),
        ]
        for cfg_key, attr_name, conv in field_map:
            if cfg_key in body and body[cfg_key] is not None:
                val = conv(body[cfg_key])
                self.config[cfg_key] = val
                setattr(plugin, attr_name, val)

        if isinstance(body.get("artist_slots"), list) and body["artist_slots"]:
            await asyncio.to_thread(plugin._save_artist_slots, body["artist_slots"])

        self.config.save_config()
        if plugin.enable_grimoire:
            return await self.get_settings_dict()

    async def execute_generate_tool(
        self,
        args: dict[str, Any],
        session_id: str = "",
    ) -> dict[str, Any]:
        plugin = await self.ensure_initialized()
        raw_prompt = str(args.get("prompt") or "").strip()
        if not raw_prompt:
            return {"success": False, "error": "提示词 (prompt) 不能为空"}

        if plugin.call_mode == "openai":
            if not plugin.openai_api_base_url:
                return {"success": False, "error": "尚未配置 OpenAI 兼容生图地址，请在「插件管理 → NovelAI 生图 → 引擎设置」中填写。"}
        else:
            if not plugin.image_gen_key:
                return {"success": False, "error": "尚未配置 NovelAI 生图 Token，请在「插件管理 → NovelAI 生图 → 引擎设置」中填写 Token。"}

        expanded_prompt = normalize_prompt(plugin._expand_grimoire(raw_prompt))
        raw_style = str(args.get("style") or "").strip()
        raw_size = str(args.get("size") or args.get("size_cn") or "").strip()
        char1 = str(args.get("char1_prompt") or "").strip()
        char2 = str(args.get("char2_prompt") or "").strip()
        negative_override = args.get("negative")
        steps_override = args.get("steps")
        scale_override = args.get("scale")
        seed_override = args.get("seed")
        try:
            count = max(1, min(4, int(args.get("count") or 1)))
        except (TypeError, ValueError):
            count = 1

        # Resolve style slot or fallback style
        style_slot = plugin._match_style_slot(raw_style) if raw_style else None
        if style_slot is None and not raw_style and plugin._artist_slots_enabled():
            style_slot = plugin._active_style_slot()

        if style_slot is not None:
            style_key = normalize_image_style(plugin.image_style) or "vertical"
            slot_model = str(style_slot.get("model") or "")
            slot_artists = str(style_slot.get("artists") or "")
            slot_label = str(style_slot.get("name") or style_key)
        else:
            slot_model = ""
            slot_artists = ""
            style_key = normalize_image_style(raw_style) if raw_style else (normalize_image_style(plugin.image_style) or "vertical")
            slot_label = IMAGE_STYLES.get(style_key, style_key)

        # Resolve character blocks
        char_blocks: list[str] = []
        if plugin._character_blocks_enabled():
            char_blocks = [
                normalize_prompt(plugin._expand_grimoire(c))
                for c in (char1, char2)
                if c
            ]

        characters: list[tuple[str, float, float]] | None = None
        final_prompt = expanded_prompt
        if char_blocks and plugin.call_mode == "openai":
            characters = (
                [(char_blocks[0], 0.5, 0.5)]
                if len(char_blocks) == 1
                else [(char_blocks[0], 0.25, 0.5), (char_blocks[1], 0.75, 0.5)]
            )
        elif char_blocks:
            wrapped: list[str] = []
            for idx, blk in enumerate(char_blocks[:2]):
                if len(char_blocks) > 1:
                    pos = "{位置左}" if idx == 0 else "{位置右}"
                    wrapped.append(f"{{人物 [{blk}], {pos} 人物}}")
                else:
                    wrapped.append(f"{{人物 [{blk}] 人物}}")
            final_prompt = normalize_prompt(", ".join([expanded_prompt] + wrapped))

        # Generate images
        saved_paths: list[str] = []
        media_tags: list[str] = []
        out_dir = _generated_images_dir(self.hermes_home)

        if plugin.call_mode == "openai":
            size_val = raw_size or plugin.image_size
            norm_s = normalize_image_size(size_val)
            if norm_s:
                size_val = plugin._openai_size(norm_s)
            size_px = plugin._normalize_openai_size(size_val)
            oa_prompt, _, _ = await plugin._prepare_openai_prompt(
                final_prompt, style_key, custom_artists=slot_artists or None
            )
            images, reason = await plugin._openai_generate(
                oa_prompt,
                size_px,
                n=count,
                steps=int(steps_override) if steps_override is not None else None,
                scale=float(scale_override) if scale_override is not None else None,
                negative=str(negative_override) if negative_override is not None else None,
                seed=int(seed_override) if seed_override is not None else None,
                model=slot_model or None,
                characters=characters,
            )
            if not images:
                return {"success": False, "error": f"生图失败: {reason or 'unknown'}"}
            for img_bytes in images:
                ext = _sniff_ext(img_bytes)
                fpath = out_dir / f"nai-{uuid.uuid4().hex[:12]}.{ext}"
                fpath.write_bytes(img_bytes)
                saved_paths.append(str(fpath))
                media_tags.append(f"MEDIA:{fpath}")
                if session_id:
                    record_pending_media(session_id, fpath)
            used_model = slot_model or plugin.openai_api_model
            used_size = size_px
        else:
            size_cn = normalize_image_size(raw_size) if raw_size else plugin.image_size
            if not size_cn:
                size_cn = plugin.image_size or "竖图"
            for _ in range(count):
                img_bytes, reason = await plugin._generate_one_custom(
                    final_prompt,
                    style_key,
                    size_cn,
                    steps=int(steps_override) if steps_override is not None else None,
                    scale=float(scale_override) if scale_override is not None else None,
                    negative=str(negative_override) if negative_override is not None else None,
                    model=slot_model or None,
                    custom_artists=slot_artists or None,
                )
                if not img_bytes:
                    if not saved_paths:
                        return {"success": False, "error": f"生图失败: {reason or 'unknown'}"}
                    break
                ext = _sniff_ext(img_bytes)
                fpath = out_dir / f"nai-{uuid.uuid4().hex[:12]}.{ext}"
                fpath.write_bytes(img_bytes)
                saved_paths.append(str(fpath))
                media_tags.append(f"MEDIA:{fpath}")
                if session_id:
                    record_pending_media(session_id, fpath)
            used_model = slot_model or plugin.model
            used_size = size_cn

        return {
            "success": True,
            "images": saved_paths,
            "media_tag": "\n".join(media_tags),
            "model": used_model,
            "style": slot_label,
            "size": used_size,
            "prompt": final_prompt,
            "note": "图片已生成。请在回复中单独一行原样保留 media_tag（或直接回复自然语言，系统会自动附带图片发送给用户）。",
        }

    async def execute_search_grimoire(self, query: str, limit: int = 10) -> dict[str, Any]:
        plugin = await self.ensure_initialized()
        lim = max(1, min(30, int(limit or 10)))
        hits = await asyncio.to_thread(search_grimoire, str(query or ""), lim, plugin._active_model_ver())
        return {
            "success": True,
            "query": query,
            "model_ver": plugin._active_model_ver(),
            "count": len(hits),
            "items": [
                {
                    "id": h.get("id"),
                    "title": h.get("title"),
                    "category": h.get("category"),
                    "volume": h.get("volume"),
                    "model_ver": h.get("model_ver"),
                    "clean_tags": h.get("clean_tags"),
                }
                for h in hits
            ],
        }

    async def execute_quota(self) -> dict[str, Any]:
        plugin = await self.ensure_initialized()
        if not plugin.image_gen_key:
            return {"success": False, "error": "尚未配置直连生图 Token (NAI_IMAGE_TOKEN)"}
        res = await plugin._fetch_quota()
        if not res.get("ok"):
            return {"success": False, "error": str(res.get("message") or "查询失败")}
        return {
            "success": True,
            "remaining": res.get("value"),
            "enabled": bool(res.get("enabled", True)),
        }


import sys as _sys

_RUNTIMES_LOCK = threading.Lock()


def get_nai_runtime(hermes_home: str | Path | None = None) -> NaiImageHostRuntime:
    if hermes_home is None:
        from hermes_constants import get_hermes_home

        home_path = Path(get_hermes_home()).resolve()
    else:
        home_path = Path(hermes_home).resolve()
    key = str(home_path)
    wmod = _sys.modules.get("astrbot.api.web")
    if wmod is not None:
        store = getattr(wmod, "_nai_runtimes", None)
        if store is None:
            store = {}
            wmod._nai_runtimes = store  # type: ignore[attr-defined]
    else:
        store = {}
    with _RUNTIMES_LOCK:
        rt = store.get(key)
        if rt is None:
            rt = NaiImageHostRuntime(home_path)
            store[key] = rt
        return rt
