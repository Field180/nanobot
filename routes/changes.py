"""
Change Set Routes (P0-1 extraction from server_final.py)

Edit transaction approval/rejection endpoints.
"""
import logging
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from edit_transaction import accept_change_set, reject_change_set, list_pending_change_sets

logger = logging.getLogger(__name__)
router = APIRouter(tags=["changes"])


def _transition_task_after_approval(change_set: dict, action: str) -> None:
    """Transition the task out of waiting_approval after user accepts/rejects.

    Without this, the task stays stuck in waiting_approval because the
    agentic loop's recovery mechanism has a 30-second cooldown guard that
    blocks recovery on the immediate resume turn.
    """
    session_id = change_set.get("session_id", "")
    if not session_id:
        return
    try:
        from task_store import get_task_store
        store = get_task_store(session_id)
        root = store.get_latest_root_task()
        if root is None or root.state != "waiting_approval":
            return
        # Check if any other pending change sets remain for this session
        remaining = list_pending_change_sets(session_id=session_id)
        if remaining:
            logger.debug("[ApprovalRoute] %d pending CS remain for session=%s, staying in waiting_approval",
                         len(remaining), session_id)
            return
        target_state = "in_progress" if action == "accept" else "planned"
        store.transition(
            root.id,
            target_state,
            source="approval_route",
            reason=f"user {action}ed change set {change_set.get('id', '?')}; no pending CS remain",
        )
        logger.info("[ApprovalRoute] %s → %s (user %sed CS %s)",
                     "waiting_approval", target_state, action, change_set.get("id", "?"))
    except Exception as exc:
        logger.warning("[ApprovalRoute] Task transition after %s failed (non-fatal): %s "
                       "[session=%s, cs=%s] — agentic loop recovery will retry",
                       action, exc, session_id, change_set.get("id", "?"))


@router.get("/api/changes/pending")
async def get_pending_change_sets_route(session_id: Optional[str] = None):
    return {
        "success": True,
        "change_sets": list_pending_change_sets(session_id=session_id),
    }


@router.post("/api/changes/{change_set_id}/accept")
async def accept_pending_change_set(change_set_id: str):
    result = accept_change_set(change_set_id)
    if not result.get("success"):
        return JSONResponse(status_code=400, content=result)
    _transition_task_after_approval(result.get("change_set", {}), "accept")
    return result


@router.post("/api/changes/{change_set_id}/reject")
async def reject_pending_change_set(change_set_id: str):
    result = reject_change_set(change_set_id)
    if not result.get("success"):
        return JSONResponse(status_code=400, content=result)
    _transition_task_after_approval(result.get("change_set", {}), "reject")
    return result
