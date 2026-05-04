"""
Nanobot Route Registry
======================
All API route modules are registered here via FastAPI APIRouter.
server_final.py calls register_all_routes(app) at startup.
"""
from fastapi import FastAPI


def register_all_routes(app: FastAPI) -> None:
    """Mount all extracted route modules onto the FastAPI app."""
    from routes.changes import router as changes_router
    from routes.safety import router as safety_router
    from routes.rate_limit import router as rate_limit_router
    from routes.permissions import router as permissions_router
    from routes.dingtalk import router as dingtalk_router
    from routes.advanced_ai import router as advanced_ai_router
    from routes.v3_neuracore import router as v3_neuracore_router
    from routes.feature_flags import router as feature_flags_router

    app.include_router(changes_router)
    app.include_router(safety_router)
    app.include_router(rate_limit_router)
    app.include_router(permissions_router)
    app.include_router(dingtalk_router)
    app.include_router(advanced_ai_router)
    app.include_router(v3_neuracore_router)
    app.include_router(feature_flags_router)
