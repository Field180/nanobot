#!/bin/bash
# Nanobot Web UI 启动脚本 (包含 Gateway 自动启动)

# 颜色定义
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║      🚀 Nanobot Web UI 启动器         ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════╝${NC}"
echo ""

# 工作目录
WORKSPACE="$HOME/.nanobot/workspace"
WEB_UI_DIR="$WORKSPACE/web_ui"
VENV_PATH="/home/field/nanobotProjects/nanobot/.venv"
GATEWAY_LOG="/tmp/nanobot_gateway.log"
GATEWAY_PID_FILE="/tmp/nanobot_gateway.pid"

# 检查 nanobot gateway 是否运行
check_gateway() {
    if nc -z localhost 18790 2>/dev/null; then
        return 0
    else
        return 1
    fi
}

# 启动 Gateway
start_gateway() {
    echo -e "${YELLOW}⚠ Gateway 未运行${NC}"
    echo -e "${YELLOW}  正在启动 Gateway...${NC}"
    
    # 使用虚拟环境中的 nanobot
    if [ -f "$VENV_PATH/bin/nanobot" ]; then
        NANOBOT="$VENV_PATH/bin/nanobot"
    else
        NANOBOT="nanobot"
    fi
    
    # 后台启动 gateway 并记录日志
    nohup $NANOBOT gateway > "$GATEWAY_LOG" 2>&1 &
    GATEWAY_PID=$!
    echo $GATEWAY_PID > "$GATEWAY_PID_FILE"
    
    # 等待 gateway 启动
    echo -n "等待 Gateway 启动"
    for i in {1..30}; do
        sleep 1
        if check_gateway; then
            echo -e "\n${GREEN}✓ Gateway 启动成功 (PID: $GATEWAY_PID)${NC}"
            return 0
        fi
        echo -n "."
    done
    
    echo -e "\n${RED}✗ Gateway 启动超时${NC}"
    echo -e "${YELLOW}查看日志: tail -f $GATEWAY_LOG${NC}"
    return 1
}

# 检查 Python 环境
echo -e "${YELLOW}📦 检查 Python 环境...${NC}"
if [ -f "$VENV_PATH/bin/python3" ]; then
    PYTHON="$VENV_PATH/bin/python3"
    echo -e "${GREEN}✓ 使用虚拟环境 Python${NC}"
else
    PYTHON="python3"
    echo -e "${YELLOW}⚠ 使用系统 Python${NC}"
fi

# 检查依赖
echo ""
echo -e "${YELLOW}📦 检查依赖...${NC}"
$PYTHON -c "import fastapi, uvicorn" 2>/dev/null || {
    echo -e "${YELLOW}安装 FastAPI 和 Uvicorn...${NC}"
    $PYTHON -m pip install fastapi uvicorn -q
}

echo -e "${GREEN}✓ 依赖已就绪${NC}"

# 检查并启动 Gateway
echo ""
echo -e "${YELLOW}🔌 检查 Nanobot Gateway...${NC}"
if check_gateway; then
    echo -e "${GREEN}✓ Gateway 已运行 (端口 18790)${NC}"
else
    start_gateway
fi

# 启动 Web UI 服务器
echo ""
echo -e "${GREEN}🌐 启动 Web UI 服务器...${NC}"
echo "═══════════════════════════════════════"
echo "  访问地址: http://localhost:8080"
echo "═══════════════════════════════════════"
echo ""

# 使用正确的服务器文件
if [ -f "server_final.py" ]; then
    SERVER_FILE="server_final.py"
elif [ -f "server_v2.py" ]; then
    SERVER_FILE="server_v2.py"
else
    SERVER_FILE="server.py"
fi

$PYTHON "$SERVER_FILE"
