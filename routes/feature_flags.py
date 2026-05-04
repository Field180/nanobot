"""
Feature Flags API Routes
========================
Provides HTTP endpoints for inspecting and toggling feature flags at runtime.
Mirrors Claw's runtime config pattern (growthbook.ts) for Nanobot.

Endpoints:
  GET  /api/feature-flags          — List all flags with current values
  GET  /api/feature-flags/{name}   — Get a single flag's info
  POST /api/feature-flags/{name}   — Set a runtime override
  DELETE /api/feature-flags/{name} — Clear a runtime override
  POST /api/feature-flags/reload   — Hot-reload config file
"""
import logging
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Any, Optional

from utils.feature_flags import ff

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feature-flags", tags=["feature-flags"])


class FlagOverrideRequest(BaseModel):
    value: Any


@router.get("")
async def list_feature_flags():
    """List all registered feature flags with current resolved values."""
    return {"flags": ff.list_flags()}


@router.get("/{name}")
async def get_feature_flag(name: str):
    """Get detailed info for a single feature flag."""
    info = ff.get_flag_info(name)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Flag '{name}' not found")
    return info


@router.post("/{name}")
async def set_feature_flag_override(name: str, body: FlagOverrideRequest):
    """Set a runtime override for a feature flag."""
    ff.set_override(name, body.value)
    logger.info("[FeatureFlags] Override set: %s = %s", name, body.value)
    info = ff.get_flag_info(name)
    return {"status": "ok", "flag": info or {"name": name, "current": body.value, "source": "override"}}


@router.delete("/{name}")
async def clear_feature_flag_override(name: str):
    """Clear a runtime override, reverting to env/config/default."""
    ff.clear_override(name)
    logger.info("[FeatureFlags] Override cleared: %s", name)
    info = ff.get_flag_info(name)
    return {"status": "ok", "flag": info or {"name": name}}


@router.post("/reload")
async def reload_feature_flags():
    """Hot-reload feature flags from config file."""
    ff.reload()
    return {"status": "ok", "message": "Config reloaded", "flags": ff.list_flags()}
