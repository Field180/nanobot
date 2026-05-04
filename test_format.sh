#!/bin/bash
# 测试后端模型代码块格式

API_URL="http://localhost:5000"  # 修改为你的实际后端地址

PROMPT='请严格按照以下格式返回3种编程语言示例：
1. 代码块使用 ```语言 开头
2. 代码块闭合后必须有空行
3. 然后是 ### 标题
4. 然后是解释文字
5. 每组之间有空行

格式示例：
```python
print("hello")
```

### Python
解释文字

```javascript
console.log("hello");
```

### JavaScript
解释文字'

echo "发送测试请求到后端..."
curl -X POST "${API_URL}/api/chat" \
  -H "Content-Type: application/json" \
  -d "{
    \"message\": \"${PROMPT}\",
    \"model\": \"qwen3.5:122b\",
    \"stream\": false
  }" \
  -s | jq -r '.response' 2>/dev/null || echo "请安装 jq 或检查API端点"
