"""
V3 Agent & NeuraCore Routes (P0-1 extraction from server_final.py)

Nanobot V3 OS Agent API and NeuraCore SNN endpoints.
"""
import asyncio
import json
import logging
import sys
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from server_state import WORKSPACE, check_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(tags=["v3-neuracore"])

# ── V3 Agent singleton ──────────────────────────────────────
_v3_agent = None
_v3_agent_initialized = False


def get_v3_agent():
    """获取或初始化 Nanobot V3 智能体"""
    global _v3_agent, _v3_agent_initialized

    if _v3_agent is None and not _v3_agent_initialized:
        try:
            sys.path.insert(0, '/home/field/nanobot_v2')
            from nanobot_v3_integrated import OSAgentIntegrated

            logger.info("[Nanobot V3] 初始化OS智能体...")
            _v3_agent = OSAgentIntegrated(
                cycle_interval=30.0,
                working_dir="/tmp"
            )
            _v3_agent_initialized = True
            logger.info("[Nanobot V3] 初始化完成!")
        except Exception as e:
            logger.error(f"[Nanobot V3] 初始化失败: {e}")
            _v3_agent_initialized = True
            return None

    return _v3_agent


# ── V3 endpoints ────────────────────────────────────────────

@router.post("/api/v3/os_agent/stream")
async def api_v3_os_agent_stream(request: Request):
    """Nanobot V3 流式API - 分块返回认知过程"""
    client_ip = request.client.host
    rate_result = check_rate_limit(client_ip, "stream")
    if not rate_result[0]:
        raise HTTPException(status_code=429, detail=f"请求过于频繁，请等待 {rate_result[1]} 秒后重试")

    try:
        data = await request.json()
        context = data.get("context", "")
        message_history = data.get("history", [])

        if not context:
            return JSONResponse({"success": False, "error": "上下文不能为空"})

        agent = get_v3_agent()

        if agent is None:
            return JSONResponse({"success": False, "error": "Nanobot V3 正在初始化中"})

        if message_history and hasattr(agent, 'context_compressor'):
            compressed_context = agent.context_compressor.compress_for_v3(message_history, context)
            logger.info(f"[V3] 上下文压缩: {len(context)}字符 -> {len(compressed_context)}字符")
        else:
            compressed_context = context

        async def stream_generator():
            try:
                for chunk in agent.process_cycle_streaming(compressed_context):
                    yield f"data: {json.dumps(chunk)}\n\n"
                    await asyncio.sleep(0.01)

                yield "data: [DONE]\n\n"
            except Exception as e:
                logger.error(f"[V3 Stream] 错误: {e}")
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"

        return StreamingResponse(
            stream_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no"
            }
        )

    except Exception as e:
        logger.error(f"[V3 Stream API] 错误: {e}")
        return JSONResponse({"success": False, "error": str(e)})


@router.post("/api/v3/os_agent")
async def api_v3_os_agent(request: Request):
    """Nanobot V3 OS智能体API"""
    client_ip = request.client.host
    rate_result = check_rate_limit(client_ip, "chat")
    if not rate_result[0]:
        raise HTTPException(status_code=429, detail=f"请求过于频繁，请等待 {rate_result[1]} 秒后重试")

    try:
        data = await request.json()
        context = data.get("context", "")
        expected_command = data.get("expected_command")

        if not context:
            return JSONResponse({
                "success": False,
                "error": "上下文不能为空"
            })

        agent = get_v3_agent()

        if agent is None:
            return JSONResponse({
                "success": False,
                "error": "Nanobot V3 正在初始化中，请稍后再试"
            })

        start_time = time.time()

        result = agent.process_cycle(
            context_text=context,
            expected_command=expected_command
        )

        elapsed_ms = (time.time() - start_time) * 1000

        stats = result.get('stats', {})
        command = result.get('command', 'unknown')

        response_text = f"""**Nanobot V3 认知结果**

**大脑活动:**
- 感觉皮层: {stats.get('sensory_spikes', 0):,} 脉冲
- 联合皮层: {stats.get('association_spikes', 0):,} 脉冲  
- 决策皮层: {stats.get('decision_spikes', 0):,} 脉冲 (STDP学习)
- 运动皮层: {stats.get('motor_spikes', 0):,} 脉冲
- 多巴胺水平: {stats.get('dopamine', 0.1):.3f}

**预测命令:** `{command}`

**处理时间:** {elapsed_ms:.1f}ms

---
*Nanobot V3 - 2.7M神经元OS智能体*"""

        return JSONResponse({
            "success": True,
            "command": command,
            "stats": stats,
            "response": response_text,
            "elapsed_ms": elapsed_ms
        })

    except Exception as e:
        logger.error(f"[Nanobot V3 API] 错误: {e}")
        return JSONResponse({
            "success": False,
            "error": str(e)
        })


@router.get("/api/v3/status")
async def api_v3_status():
    """获取 Nanobot V3 状态"""
    agent = get_v3_agent()

    if agent is None:
        return JSONResponse({
            "initialized": False,
            "ready": False,
            "message": "Nanobot V3 未初始化"
        })

    return JSONResponse({
        "initialized": True,
        "ready": True,
        "total_attempts": agent.total_attempts,
        "success_count": agent.success_count,
        "accuracy": agent.success_count / max(1, agent.total_attempts),
        "brain_neurons": 6000000,
        "sensory_neurons": 800000,
        "association_neurons": 1600000,
        "decision_neurons": 2500000,
        "prefrontal_neurons": 800000,
        "motor_neurons": 340000
    })


# ── NeuraCore endpoints ─────────────────────────────────────

@router.post("/api/neuracore/stream")
async def neuracore_stream_endpoint(request: Request):
    """NeuraCore神经核心流式端点 - 支持多种SNN模式"""
    from neuracore_backend import api_neuracore_stream, init_neuracore

    init_neuracore()

    data = await request.json()

    async def event_generator():
        async for event in api_neuracore_stream(data):
            yield event

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.post("/api/neuracore")
async def neuracore_endpoint(request: Request):
    """NeuraCore神经核心API端点 - 300万神经元SNN意识主体"""
    from neuracore_backend import api_neuracore, init_neuracore

    init_neuracore()

    data = await request.json()
    result = await api_neuracore(data)
    return JSONResponse(result)


@router.get("/api/neuracore/status")
async def neuracore_status():
    """NeuraCore状态检查"""
    from neuracore_backend import NEURACORE_ENABLED, NEURACORE_INSTANCE, init_neuracore

    if NEURACORE_INSTANCE is None:
        init_neuracore()

    return {
        "enabled": NEURACORE_ENABLED,
        "initialized": NEURACORE_INSTANCE is not None,
        "neurons": NEURACORE_INSTANCE.total_neurons if NEURACORE_INSTANCE else 0,
        "architecture": "真实SNN脉冲神经网络 (向量化构建)"
    }
