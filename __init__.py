"""NovelAI Image Generation plugin for Hermes Agent (v2.9.0)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    success_response,
)

logger = logging.getLogger("hermes.nai_image")

_GENERATE_SCHEMA: dict[str, Any] = {
    "name": "nai_generate_image",
    "description": (
        "用 NovelAI 生成二次元插画/自拍/角色图并直接发给当前用户。"
        "提示词用英文逗号分隔的 Danbooru 标签效果最好，也支持直接写 【法典:词条名】 宏。"
        "一次调用就会真实消耗点数，同一条回复中不要重复调用。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": (
                    "主体与场景正向提示词（逗号分隔的英文 태그，如 '1girl, solo, masterpiece, best quality, ...'）。"
                    "支持嵌入 【法典:词条名】 自动展开。"
                ),
            },
            "style": {
                "type": "string",
                "description": (
                    "画风槽位名称（如 '韩漫小清新风'、'漫画同人风'、'2.5D唯美风'、'2.5D唯美风（萝）'、'本子里番风'、'GalGame风'）"
                    "或风格键（vertical / comicDoujin / r18 / lolita25d / anime / galgame / custom）。留空使用插件当前配置的默认画风。"
                ),
            },
            "size": {
                "type": "string",
                "description": (
                    "画幅尺寸：竖图 / 横图 / 方图 / 2K竖图 / 2K横图 / 2K方图 / 4K竖图 / 4K横图 / 4K方图，"
                    "或像素分辨率（如 832x1216）。留空使用默认尺寸。"
                ),
            },
            "char1_prompt": {
                "type": "string",
                "description": "多角色分区的第一个角色标签（只写该角色的外貌、发色瞳色、表情、动作、服装，不写背景）。单人图可留空。",
            },
            "char2_prompt": {
                "type": "string",
                "description": "多角色分区的第二个角色标签（写法同上，左右分区独立不串色）。单人图留空。",
            },
            "negative": {
                "type": "string",
                "description": "可选：单次覆盖反向提示词。留空使用插件默认负面词。",
            },
            "steps": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "可选：采样步数 (1-50)。留空使用默认步数。",
            },
            "scale": {
                "type": "number",
                "minimum": 0,
                "maximum": 20,
                "description": "可选：提示词引导系数 (0-20)。留空使用默认值。",
            },
            "seed": {
                "type": "integer",
                "description": "可选：随机种子（-1 或留空表示随机）。",
            },
            "count": {
                "type": "integer",
                "minimum": 1,
                "maximum": 4,
                "description": "生成张数 (1-4，默认 1)。",
            },
        },
        "required": ["prompt"],
    },
}

_GRIMOIRE_SCHEMA: dict[str, Any] = {
    "name": "nai_search_grimoire",
    "description": (
        "检索内置 NovelAI 法典词库（19,663 条经实测的 V4.5/V5 标签组合），用于查询画风、服装、姿势、视角、场景等正向标签。"
        "不生图、不消耗点数。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "中文或英文检索关键词（如 '白裙'、'自拍'、'旗袍'、'夜景'、'哥特'），支持空格分隔多个词。",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 30,
                "description": "返回条数上限（默认 10，最大 30）。",
            },
        },
        "required": ["query"],
    },
}

_QUOTA_SCHEMA: dict[str, Any] = {
    "name": "nai_quota",
    "description": "查询 NovelAI 直连生图站账号的剩余点数与 Token 启用状态（不生图、不消耗点数）。",
    "parameters": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


def _resolve_active_session_id(session_id: str = "", task_id: str = "") -> str:
    sid = str(session_id or task_id or "").strip()
    if sid:
        return sid
    try:
        from tools.approval_context import _approval_session_id

        return str(_approval_session_id.get() or "").strip()
    except Exception:
        return ""


def _handle_nai_generate_image(args: dict[str, Any], session_id: str = "", task_id: str = "", **kwargs: Any) -> str:
    from .runtime_host import get_nai_runtime

    sid = _resolve_active_session_id(session_id, task_id)
    rt = get_nai_runtime()
    res = rt.run_sync(rt.execute_generate_tool(args or {}, session_id=sid), timeout=240.0)
    return json.dumps(res, ensure_ascii=False)


def _handle_nai_search_grimoire(args: dict[str, Any], **kwargs: Any) -> str:
    from .runtime_host import get_nai_runtime

    rt = get_nai_runtime()
    q = str((args or {}).get("query") or "")
    lim = int((args or {}).get("limit") or 10)
    res = rt.run_sync(rt.execute_search_grimoire(q, limit=lim), timeout=30.0)
    return json.dumps(res, ensure_ascii=False)


def _handle_nai_quota(args: dict[str, Any], **kwargs: Any) -> str:
    from .runtime_host import get_nai_runtime

    rt = get_nai_runtime()
    res = rt.run_sync(rt.execute_quota(), timeout=20.0)
    return json.dumps(res, ensure_ascii=False)


class NovelAIImageGenProvider(ImageGenProvider):
    """Also exposes NovelAI as a selectable Hermes ``image_gen.provider: nai`` backend."""

    @property
    def name(self) -> str:
        return "nai"

    @property
    def display_name(self) -> str:
        return "NovelAI (NAI 4.5 / V5)"

    def is_available(self) -> bool:
        return True

    def list_models(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "nai-diffusion-4-5-full",
                "display": "NAI Diffusion V4.5 Full",
                "speed": "~12s",
                "strengths": "二次元角色、画风槽位、法典词条与多角色分区",
            },
            {
                "id": "nai-diffusion-5-full",
                "display": "NAI Diffusion V5 Full",
                "speed": "~15s",
                "strengths": "最新 V5 底模，细节与光影表现更强",
            },
        ]

    def default_model(self) -> str:
        return "nai-diffusion-4-5-full"

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        *,
        image_url: str | None = None,
        reference_image_urls: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        from .runtime_host import get_nai_runtime

        size_map = {"portrait": "竖图", "landscape": "横图", "square": "方图"}
        sid = _resolve_active_session_id()
        rt = get_nai_runtime()
        res = rt.run_sync(
            rt.execute_generate_tool(
                {"prompt": prompt, "size": size_map.get(aspect_ratio, "竖图")},
                session_id=sid,
            ),
            timeout=240.0,
        )
        if not res.get("success") or not res.get("images"):
            return error_response(
                error=str(res.get("error") or "NovelAI generation failed"),
                provider="nai",
                model=str(res.get("model") or "nai-diffusion-4-5-full"),
                prompt=prompt,
                aspect_ratio=aspect_ratio,
            )
        return success_response(
            image=res["images"][0],
            model=str(res.get("model") or "nai-diffusion-4-5-full"),
            prompt=prompt,
            aspect_ratio=aspect_ratio,
            provider="nai",
        )


def register(ctx: Any) -> None:
    """Register NovelAI tools, output hook, skill, and image_gen provider."""
    from .runtime_host import on_transform_llm_output

    if hasattr(ctx, "register_tool"):
        ctx.register_tool(
            name="nai_generate_image",
            toolset="nai_image",
            schema=_GENERATE_SCHEMA,
            handler=_handle_nai_generate_image,
            check_fn=lambda: True,
            description=_GENERATE_SCHEMA["description"],
            emoji="🎨",
        )
        ctx.register_tool(
            name="nai_search_grimoire",
            toolset="nai_image",
            schema=_GRIMOIRE_SCHEMA,
            handler=_handle_nai_search_grimoire,
            check_fn=lambda: True,
            description=_GRIMOIRE_SCHEMA["description"],
            emoji="📖",
        )
        ctx.register_tool(
            name="nai_quota",
            toolset="nai_image",
            schema=_QUOTA_SCHEMA,
            handler=_handle_nai_quota,
            check_fn=lambda: True,
            description=_QUOTA_SCHEMA["description"],
            emoji="💎",
        )

    if hasattr(ctx, "register_hook"):
        ctx.register_hook("transform_llm_output", on_transform_llm_output)

    if hasattr(ctx, "register_image_gen_provider"):
        ctx.register_image_gen_provider(NovelAIImageGenProvider())

    if hasattr(ctx, "register_skill"):
        skill_path = Path(__file__).resolve().parent / "skills" / "nai-anime-illustration" / "SKILL.md"
        if skill_path.is_file():
            ctx.register_skill(
                "nai-anime-illustration",
                skill_path,
                description="NovelAI V4.5 & V5 anime image generation with built-in 19,663-entry Grimoire, style slots, and multi-character blocks",
            )
