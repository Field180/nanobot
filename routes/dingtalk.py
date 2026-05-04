"""
DingTalk Channel Routes (P0-1 extraction from server_final.py)

DingTalk bot webhook, AES encrypt/decrypt, signature verification.
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import struct
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import requests
from Crypto.Cipher import AES
from fastapi import APIRouter, Request

from server_state import (
    WORKSPACE, PYTHON, sessions, clean_ansi, get_system_resources,
    safe_get_or_create_session,
)
from session_persistence import save_session_history
from config_manager import THINKING_PROMPT

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dingtalk"])

# ── DingTalk config from env ────────────────────────────────
DINGTALK_TOKEN = os.environ.get("DINGTALK_TOKEN", "")
DINGTALK_AES_KEY = os.environ.get("DINGTALK_AES_KEY", "")
DINGTALK_APP_KEY = os.environ.get("DINGTALK_APP_KEY", "")
DINGTALK_CUSTOM_WEBHOOK = os.environ.get("DINGTALK_CUSTOM_WEBHOOK", "")
DINGTALK_CUSTOM_SECRET = os.environ.get("DINGTALK_CUSTOM_SECRET", "")


# ── Crypto helpers ──────────────────────────────────────────

def send_custom_dingtalk_message(message: str) -> None:
    """向自定义钉钉机器人发送文本消息，自动带上关键词 nano。"""
    if not DINGTALK_CUSTOM_WEBHOOK or not message:
        return

    text = message.strip()
    if not text:
        return

    payload = {
        "msgtype": "text",
        "text": {"content": f"nano\n{text}"[:1800]}
    }

    url = DINGTALK_CUSTOM_WEBHOOK
    if DINGTALK_CUSTOM_SECRET:
        timestamp = str(int(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{DINGTALK_CUSTOM_SECRET}"
        sign = base64.b64encode(
            hmac.new(DINGTALK_CUSTOM_SECRET.encode(), string_to_sign.encode(), hashlib.sha256).digest()
        ).decode()
        url = f"{url}&{urlencode({'timestamp': timestamp, 'sign': sign})}"

    try:
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code != 200:
            logger.warning(f"[钉钉] 自定义机器人发送失败: {response.text}")
    except Exception as exc:
        logger.warning(f"[钉钉] 自定义机器人请求异常: {exc}")


def dingtalk_decrypt(encrypt_text: str, aes_key: str, app_key: str = "") -> str:
    """钉钉 AES 解密"""
    try:
        key = base64.b64decode(aes_key + "=")
        encrypt_data = base64.b64decode(encrypt_text)

        iv = key[:16]
        cipher = AES.new(key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(encrypt_data)

        pad_length = decrypted[-1]
        decrypted = decrypted[:-pad_length]

        msg_len = struct.unpack("!I", decrypted[16:20])[0]
        msg = decrypted[20:20+msg_len].decode('utf-8')

        return msg
    except Exception as e:
        logger.error(f"[钉钉] 解密失败: {e}")
        return ""


def dingtalk_encrypt(plain_text: str, aes_key: str, app_key: str = "") -> str:
    """钉钉 AES 加密"""
    try:
        key = base64.b64decode(aes_key + "=")

        random_bytes = os.urandom(16)
        msg_bytes = plain_text.encode('utf-8')
        msg_len = struct.pack("!I", len(msg_bytes))
        app_key_bytes = app_key.encode('utf-8') if app_key else b""

        data = random_bytes + msg_len + msg_bytes + app_key_bytes

        pad_length = 16 - (len(data) % 16)
        data += bytes([pad_length] * pad_length)

        iv = key[:16]
        cipher = AES.new(key, AES.MODE_CBC, iv)
        encrypted = cipher.encrypt(data)

        return base64.b64encode(encrypted).decode('utf-8')
    except Exception as e:
        logger.error(f"[钉钉] 加密失败: {e}")
        return ""


def verify_dingtalk_signature(token: str, timestamp: str, nonce: str, encrypt_text: str) -> str:
    """计算钉钉签名"""
    try:
        data_list = [token, timestamp, nonce, encrypt_text]
        data_list.sort()
        data_str = "".join(data_list)
        return hashlib.sha1(data_str.encode()).hexdigest()
    except Exception:
        return ""


def _extract_response(output: str, message: str) -> str:
    """Extract response text from nanobot CLI output (simplified)."""
    # Import the full extract_response from server_final at call time
    # to avoid circular import at module load
    try:
        from server_final import extract_response
        return extract_response(output, message)
    except ImportError:
        # Fallback: return raw output stripped
        return output.strip()


# ── Webhook endpoint ────────────────────────────────────────

@router.post("/api/dingtalk/webhook")
async def dingtalk_webhook(request: Request):
    """钉钉机器人 Webhook 回调端点（支持企业机器人加解密）"""
    try:
        params = dict(request.query_params)
        signature = params.get("signature", "")
        msg_signature = params.get("msg_signature", "")
        timestamp = params.get("timestamp", "")
        nonce = params.get("nonce", "")

        body = await request.json()
        encrypt_text = body.get("encrypt", "")

        logger.info(f"[钉钉] 收到回调: signature={signature[:16]}..., timestamp={timestamp}")

        # 解密
        msg_content = ""
        if encrypt_text and DINGTALK_AES_KEY:
            computed_sig = verify_dingtalk_signature(DINGTALK_TOKEN, timestamp, nonce, encrypt_text)
            if msg_signature and computed_sig != msg_signature:
                logger.warning(f"[钉钉] 签名验证失败")

            msg_content = dingtalk_decrypt(encrypt_text, DINGTALK_AES_KEY, DINGTALK_APP_KEY)
            if not msg_content:
                return {"errcode": 40003, "errmsg": "decrypt failed"}
        elif body.get("text"):
            msg_content = json.dumps(body)

        # 解析消息内容
        try:
            msg_data = json.loads(msg_content) if msg_content else body
        except Exception:
            msg_data = body

        # 获取消息文本
        content = ""
        if isinstance(msg_data, dict):
            if "text" in msg_data and isinstance(msg_data["text"], dict):
                content = msg_data["text"].get("content", "").strip()
            elif "content" in msg_data:
                content = msg_data.get("content", "").strip()
            elif "msgtype" in msg_data and msg_data["msgtype"] == "text":
                content = msg_data.get("text", {}).get("content", "").strip()

        # URL 验证请求
        if not content and encrypt_text:
            return {
                "errcode": 0,
                "errmsg": "ok",
                "encrypt": dingtalk_encrypt("success", DINGTALK_AES_KEY, DINGTALK_APP_KEY)
            }

        # 发送者信息
        sender = msg_data.get("senderStaffId", msg_data.get("staffId", "ding_user"))
        sender_nick = msg_data.get("senderNick", "钉钉用户")

        session_id = f"ding_{sender}"

        logger.info(f"[钉钉] 收到消息: {content[:50]}... 来自: {sender_nick}")

        if not content:
            response_text = "请发送具体消息内容"
        elif content in ["帮助", "help", "?"]:
            response_text = "**🤖 Nanobot 钉钉机器人**\n\n• 直接发送消息即可对话\n• 支持自然语言问答\n• 发送 `状态` 查看系统状态"
        elif content in ["状态", "status"]:
            resources = get_system_resources()
            response_text = f"📊 系统状态\nCPU: {resources.get('cpu_percent', 'N/A')}%\n内存: {resources.get('memory_percent', 'N/A')}%\n磁盘: {resources.get('disk_percent', 'N/A')}%"
        else:
            try:
                env = {
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "HOME": os.environ.get("HOME", "/tmp"),
                    "NANOBOT_WORKSPACE": str(WORKSPACE),
                    "NANOBOT_MAX_TOOL_ITERATIONS": "50",
                    "NANOBOT_ENABLE_ALL_TOOLS": "true"
                }

                full_message = f"System: {THINKING_PROMPT}\n\nUser: {content}"

                result = subprocess.run(
                    [PYTHON, "-m", "nanobot", "agent", "-m", full_message, "--raw", "--no-logs"],
                    capture_output=True,
                    text=True,
                    timeout=300,
                    cwd=str(WORKSPACE),
                    env=env
                )

                output = clean_ansi(result.stdout)
                response_text = _extract_response(output, full_message)

                if not response_text:
                    response_text = "⏳ 模型正在加载中，请稍后重试"

                # 保存会话历史 (lock-guarded create-if-missing)
                sess = await safe_get_or_create_session(session_id)
                history = sess["history"]
                history.append({
                    "role": "user",
                    "content": content,
                    "time": datetime.now().isoformat()
                })
                history.append({
                    "role": "assistant",
                    "content": response_text,
                    "time": datetime.now().isoformat()
                })
                save_session_history(session_id, history)

                if len(history) > 20:
                    sessions[session_id]["history"] = history[-20:]

                logger.info(f"[钉钉] 回复: {response_text[:50]}...")

            except Exception as e:
                logger.error(f"[钉钉] 处理失败: {e}")
                response_text = f"❌ 处理失败: {str(e)[:200]}"

        # 截断过长消息
        if len(response_text) > 4000:
            response_text = response_text[:4000] + "\n\n...(消息过长已截断)"

        # 推送到自定义机器人
        try:
            send_custom_dingtalk_message(response_text)
        except Exception as exc:
            logger.warning(f"[钉钉] 推送自定义机器人失败: {exc}")

        # 构建响应
        response_data = {
            "errcode": 0,
            "errmsg": "ok",
            "text": response_text
        }

        if DINGTALK_AES_KEY:
            response_json = json.dumps(response_data)
            encrypt_response = dingtalk_encrypt(response_json, DINGTALK_AES_KEY, DINGTALK_APP_KEY)
            return {
                "errcode": 0,
                "errmsg": "ok",
                "encrypt": encrypt_response
            }

        return response_data

    except Exception as e:
        logger.error(f"[钉钉] Webhook 异常: {e}")
        return {"errcode": 500, "errmsg": "internal error"}
