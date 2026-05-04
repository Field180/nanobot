#!/bin/bash
# 快速测试 Nanobot Web UI

echo "🧪 测试 Nanobot Web UI"
echo "======================"
echo ""

WEB_DIR="$HOME/.nanobot/workspace/web_ui"
cd "$WEB_DIR" || exit 1

PYTHON="/home/field/nanobotProjects/nanobot/.venv/bin/python3"

# 测试 1: 检查文件
echo "1️⃣  检查文件..."
files=("index.html" "server_simple.py" "static/js/app.js")
for f in "${files[@]}"; do
    if [ -f "$f" ]; then
        echo "   ✓ $f"
    else
        echo "   ✗ $f 缺失"
    fi
done
echo ""

# 测试 2: 检查依赖
echo "2️⃣  检查依赖..."
if $PYTHON -c "import fastapi, uvicorn" 2>/dev/null; then
    echo "   ✓ FastAPI 和 Uvicorn 已安装"
else
    echo "   ⚠ 安装依赖..."
    $PYTHON -m pip install fastapi uvicorn python-multipart -q
    echo "   ✓ 依赖安装完成"
fi
echo ""

# 测试 3: 检查 Gateway
echo "3️⃣  检查 Gateway..."
if nc -z localhost 18790 2>/dev/null; then
    echo "   ✓ Gateway 运行中 (端口 18790)"
else
    echo "   ⚠ Gateway 未运行"
    echo "      启动命令: nanobot gateway"
fi
echo ""

# 测试 4: 启动测试服务器（后台）
echo "4️⃣  启动测试服务器..."
$PYTHON server_simple.py > /tmp/web_ui_test.log 2>&1 &
PID=$!
sleep 2

if kill -0 $PID 2>/dev/null; then
    echo "   ✓ 服务器启动成功 (PID: $PID)"
    
    # 测试 API
    echo ""
    echo "5️⃣  测试 API..."
    response=$(curl -s -X POST http://localhost:8080/api/chat \
        -H "Content-Type: application/json" \
        -d '{"message":"Hello","session_id":"test"}' 2>/dev/null)
    
    if [ -n "$response" ]; then
        echo "   ✓ API 响应正常"
        echo "   响应: $(echo $response | cut -c1-100)..."
    else
        echo "   ⚠ API 无响应"
    fi
    
    # 关闭测试服务器
    kill $PID 2>/dev/null
    echo ""
    echo "   ✓ 测试服务器已关闭"
else
    echo "   ✗ 服务器启动失败"
    echo "   日志: /tmp/web_ui_test.log"
fi

echo ""
echo "======================"
echo "✅ 测试完成"
echo ""
echo "启动 Web UI:"
echo "   cd $WEB_DIR && ./launch.sh"
echo ""
echo "或直接访问:"
echo "   http://localhost:8080"
