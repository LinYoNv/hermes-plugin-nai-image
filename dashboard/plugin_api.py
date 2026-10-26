"""FastAPI backend for NovelAI Image Generation Dashboard & Settings, mounted at /api/plugins/nai-image/."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
import sys
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from hermes_constants import get_hermes_home

router = APIRouter()
logger = logging.getLogger("hermes.nai_image.api")

_PLUGIN_ROOT = Path(__file__).resolve().parent.parent
_PLUGINS_PARENT = str(_PLUGIN_ROOT.parent)
if _PLUGINS_PARENT not in sys.path:
    sys.path.insert(0, _PLUGINS_PARENT)


def _load_pkg_mod(sub: str) -> Any:
    # Ensure canonical package alias "nai_image_hermes_pkg" in sys.modules
    pkg_name = "nai_image_hermes_pkg"
    if pkg_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            pkg_name,
            str(_PLUGIN_ROOT / "__init__.py"),
            submodule_search_locations=[str(_PLUGIN_ROOT)],
        )
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[pkg_name] = mod
        spec.loader.exec_module(mod)
    return importlib.import_module(f"{pkg_name}.{sub}")


def _get_runtime():
    rh = _load_pkg_mod("runtime_host")
    return rh.get_nai_runtime(get_hermes_home())


@router.get("/settings")
async def get_nai_settings() -> dict[str, Any]:
    rt = _get_runtime()
    return await rt.run_from_other_loop(rt.get_settings_dict(), timeout=30.0)


@router.post("/settings")
async def save_nai_settings(request: Request) -> dict[str, Any]:
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Invalid JSON object")
    rt = _get_runtime()
    return await rt.run_from_other_loop(rt.save_settings_dict(body), timeout=30.0)


@router.post("/quota")
async def check_nai_quota() -> dict[str, Any]:
    rt = _get_runtime()
    return await rt.run_from_other_loop(rt.execute_quota(), timeout=25.0)


@router.api_route("/test_panel/{subpath:path}", methods=["GET", "POST"])
async def dispatch_nai_test_panel(subpath: str, request: Request) -> Any:
    shim = _load_pkg_mod("astrbot_nai_shim")
    rt = _get_runtime()
    await rt.run_from_other_loop(rt.ensure_initialized(), timeout=30.0)

    full_route = f"/astrbot_plugin_nai_image/test_panel/{subpath.strip('/')}"
    entry = rt.context.web_routes.get(full_route)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Unknown NAI test_panel route: {subpath}")

    handler, allowed_methods, _desc = entry
    if request.method.upper() not in [m.upper() for m in allowed_methods]:
        raise HTTPException(status_code=405, detail=f"Method {request.method} not allowed")

    raw_body = await request.body()

    async def _invoke_on_rt_loop():
        with shim.bind_nai_web_request(request, raw_body=raw_body):
            return await handler()

    return await rt.run_from_other_loop(_invoke_on_rt_loop(), timeout=240.0)
