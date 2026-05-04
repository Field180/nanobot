#!/bin/bash
# Nanobot Web UI - 完整启动脚本
# 同时启动 Gateway 和 Web UI

set -e

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

WEB_DIR="$HOME/.nanobot/workspace/web_ui"
VENV="/home/field/nanobotProjects/nanobot/.venv"
PYTHON="$VENV/bin/python3"
GATEWAY_PID_FILE="/tmp/nanobot_gateway.pid"
WEB_PID_FILE="/tmp/nanobot_web.pid"

cleanup() {
    echo ""
    echo -e "${YELLOW}正在关闭服务...${NC}"
    
    if [ -f "$WEB_PID_FILE" ]; then
        kill $(cat "$WEB_PID_FILE") 2>/dev/null || true
        rm -f "$WEB_PID_FILE"
    fi
    
    # Gateway 保持运行，除非指定了 --stop-all
    if [ "$STOP_ALL" = "1" ] && [ -f "$GATEWAY_PID_FILE" ]; then
        kill $(cat "$GATEWAY_PID_FILE") 2>/dev/null || true
        rm -f "$GATEWAY_PID_FILE"
    fi
    
    exit 0
}

trap cleanup EXIT INT TERM

echo -e "${BLUE}"
echo "╔════════════════════════════════════════════════╗"
echo "║                                                ║"
echo "║         🚀 Nanobot Web UI 启动器              ║"
echo "║                                                ║"
echo "╚════════════════════════════════════════════════╝"
echo -e "${NC}"

# 检查依赖
echo -e "${YELLOW}📦 检查依赖...${NC}"
if ! $PYTHON -c "import fastapi, uvicorn" 2>/dev/null; then
    echo -e "${YELLOW}安装 FastAPI...${NC}"
    $PYTHON -m pip install fastapi uvicorn python-multipart -q
fi
echo -e "${GREEN}✓ 依赖就绪${NC}"
echo ""

# 启动 Gateway
echo -e "${YELLOW}🔌 启动 Nanobot Gateway...${NC}"
if nc -z localhost 18790 2>/dev/null; then
    echo -e "${GREEN}✓ Gateway 已在运行${NC}"
else
    cd "$HOME"
    NANOBOT_WORKSPACE="$HOME/.nanobot/workspace" \
        $PYTHON -m nanobot gateway > /tmp/nanobot_gateway.log 2>&1 &
    echo $! > "$GATEWAY_PID_FILE"
    
    echo -n "等待 Gateway 启动"
    for i in {1..20}; do
        if nc -z localhost 18790 2>/dev/null; then
            echo -e "\n${GREEN}✓ Gateway 启动成功${NC}"
            break
        fi
        echo -n "."
        sleep 1
    done
    
    if ! nc -z localhost 18790 2>/dev/null; then
        echo -e "\n${RED}✗ Gateway 启动失败${NC}"
        echo "查看日志: tail -20 /tmp/nanobot_gateway.log"
    fi
fi
echo ""

# 启动 Web UI
echo -e "${YELLOW}🌐 启动 Web UI 服务器...${NC}"
cd "$WEB_DIR"

$PYTHON server_gateway.py > /tmp/nanobot_web.log 2>&1 &
echo $! > "$WEB_PID_FILE"
sleep 2

if kill -0 $(cat "$WEB_PID_FILE") 2>/dev/null; then
    echo -e "${GREEN}✓ Web UI 启动成功${NC}"
    echo ""
    echo -e "${BLUE}════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  🎉 服务已启动!${NC}"
    echo ""
    echo -e "${GREEN}  📱 Web UI:  http://localhost:8080${NC}"
    echo -e "${YELLOW}  🔌 Gateway: http://localhost:18790${NC}"
    echo ""
    echo -e "${BLUE}════════════════════════════════════════════════${NC}"
    echo ""
    echo "快捷键:"
    echo "  - Ctrl+C 停止 Web UI"
    echo "  - 使用 './run.sh --stop-all' 停止所有服务"
    echo ""
    
    # 等待信号
    wait $(cat "$WEB_PID_FILE") 2>/dev/null || true
else
    echo -e "${RED}✗ Web UI 启动失败${NC}"
    echo "查看日志: tail -20 /tmp/nanobot_web.log"
    exit 1
fi
