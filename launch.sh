#!/bin/bash
# Nanobot Web UI 启动脚本
# 自动后台启动 nanobot gateway，然后启动 Web UI

# 颜色
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# 路径
WORKSPACE="$HOME/.nanobot/workspace"
WEB_UI_DIR="$WORKSPACE/web_ui"
VENV="/home/field/nanobotProjects/nanobot/.venv"
PYTHON="$VENV/bin/python3"
NANOBOT="$VENV/bin/nanobot"
GATEWAY_LOG="/tmp/nanobot_gateway.log"

# SSH 配置（用于远程停止 Ollama）
export NANOBOT_OLLAMA_SSH_HOST="192.168.140.1"
export NANOBOT_OLLAMA_SSH_USER="96125filed"
export NANOBOT_OLLAMA_SSH_KEY="$HOME/.ssh/nanobot_win11_ed25519"
export NANOBOT_OLLAMA_SSH_PORT="22"

# 钉钉机器人配置（事件订阅加解密用）
# 从钉钉开发者后台获取：
# DINGTALK_TOKEN = 签名 token
# DINGTALK_AES_KEY = 加密 aes_key
# DINGTALK_APP_KEY = 应用的 AppKey
export DINGTALK_TOKEN="uHERmV22sIaCKEzOLyOvhYA1BzSjgn8pF"
export DINGTALK_AES_KEY="1RZ3tYt8C4Pzb6KDnSXP38wF5TTvc7oRSpK9YhNG2we"
export DINGTALK_APP_KEY="dingqhblq3bcolh9b6m2"

show_header() {
    echo -e "${BLUE}"
    echo "╔════════════════════════════════════════╗"
    echo "║      🚀 Nanobot Web UI 启动器         ║"
    echo "╚════════════════════════════════════════╝"
    echo -e "${NC}"
}

# 检查 gateway 是否已在运行
check_gateway() {
    pgrep -f "nanobot gateway" > /dev/null 2>&1
}

# 后台启动 gateway
start_gateway() {
    if check_gateway; then
        echo -e "${GREEN}✓ Gateway 已在运行${NC}"
        return 0
    fi
    
    echo -e "${YELLOW}→ 启动 nanobot gateway (后台)...${NC}"
    
    # 后台启动 gateway
    cd "$HOME"
    nohup "$NANOBOT" gateway > "$GATEWAY_LOG" 2>&1 &
    
    # 等待初始化
    echo -n "  等待初始化"
    for i in {1..5}; do
        sleep 1
        echo -n "."
    done
    echo ""
    
    if check_gateway; then
        echo -e "${GREEN}✓ Gateway 启动成功${NC}"
    else
        echo -e "${YELLOW}⚠ Gateway 状态未知，继续启动 Web UI${NC}"
    fi
}

# 启动 cloudflared 隧道
start_cloudflared() {
    echo -e "${YELLOW}→ 启动 cloudflared 隧道...${NC}"
    
    # 检查 cloudflared 是否已安装
    if ! command -v cloudflared &> /dev/null; then
        echo -e "${YELLOW}⚠ cloudflared 未安装，跳过隧道启动${NC}"
        echo -e "   安装命令: curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -o /tmp/cloudflared && chmod +x /tmp/cloudflared && sudo mv /tmp/cloudflared /usr/local/bin/"
        return 1
    fi
    
    # 停止已有的 cloudflared
    pkill -f "cloudflared tunnel" 2>/dev/null || true
    sleep 1
    
    # 后台启动隧道，捕获 URL
    nohup cloudflared tunnel --url http://localhost:8081 > /tmp/cloudflared.log 2>&1 &
    
    echo -n "  等待隧道建立"
    TUNNEL_URL=""
    for i in {1..20}; do
        sleep 1
        echo -n "."
        # 从日志中提取 URL
        TUNNEL_URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' /tmp/cloudflared.log 2>/dev/null | head -1)
        if [ -n "$TUNNEL_URL" ]; then
            echo ""
            echo -e "${GREEN}✓ 隧道已建立: ${TUNNEL_URL}${NC}"
            echo "$TUNNEL_URL" > /tmp/cloudflared_url.txt
            return 0
        fi
    done
    
    echo ""
    echo -e "${YELLOW}⚠ 隧道启动超时，请检查 cloudflared 状态${NC}"
    return 1
}

start_web_ui() {
    echo -e "${BLUE}→ 启动 Web UI 服务器...${NC}"
    
    cd "$WEB_UI_DIR"
    
    # 检查依赖
    if ! "$PYTHON" -c "import fastapi, uvicorn" 2>/dev/null; then
        echo -e "${YELLOW}  安装依赖...${NC}"
        "$PYTHON" -m pip install fastapi uvicorn python-multipart -q
    fi
    
    echo ""
    echo -e "${GREEN}✓ 所有服务已启动${NC}"
    echo -e "${BLUE}═══════════════════════════════════════${NC}"
    echo -e "${GREEN}  Gateway: http://localhost:18790${NC}"
    echo -e "${GREEN}  Web UI:  http://localhost:8081${NC}"
    
    # 显示 cloudflared 隧道 URL（如果已启动）
    if [ -f /tmp/cloudflared_url.txt ]; then
        TUNNEL_URL=$(cat /tmp/cloudflared_url.txt)
        echo -e "${GREEN}  钉钉Webhook: ${TUNNEL_URL}/api/dingtalk/webhook${NC}"
    fi
    
    echo -e "${BLUE}═══════════════════════════════════════${NC}"
    echo ""
    
    # 启动 Web UI（前台运行）
    "$PYTHON" server_final.py
}

# 清理函数 - 退出时关闭 gateway 和 cloudflared
cleanup() {
    echo ""
    echo -e "${YELLOW}→ 正在关闭服务...${NC}"
    pkill -f "nanobot gateway" 2>/dev/null || true
    pkill -f "cloudflared tunnel" 2>/dev/null || true
    rm -f /tmp/cloudflared_url.txt 2>/dev/null || true
    exit 0
}

# 捕获退出信号
trap cleanup EXIT INT TERM

# 主程序
show_header
start_gateway
start_cloudflared
start_web_ui
