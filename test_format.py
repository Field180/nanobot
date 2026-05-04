#!/usr/bin/env python3
"""
测试后端模型代码块格式输出
检查：代码块后是否有空行、标题格式是否正确
"""

import requests
import json
import re

API_URL = "http://localhost:5000"  # 修改为你的实际后端地址

PROMPT = '''请严格按照以下格式返回5种编程语言示例：

格式要求：
1. 代码块使用 ```语言 开头和 ``` 结尾
2. 代码块闭合后必须有一个空行
3. 然后是 ### 标题（如 ### Python）
4. 然后是解释文字
5. 每组之间有一个空行分隔

正确格式示例：
```python
print("hello")
```

### Python
解释文字在这里

```javascript
console.log("hello");
```

### JavaScript
解释文字在这里'''

def test_ooo():
    print("=" * 60)
    print("测试后端模型代码块格式")
    print("=" * 60)
    
    try:
        response = requests.post(
            f"{API_URL}/api/chat",
            json={
                "message": PROMPT,
                "model": "qwen3.5:122b",
                "stream": False
            },
            timeout=60
        )
        
        if response.status_code != 200:
            print(f"❌ API请求失败: {response.status_code}")
            print(response.text)
            return
        
        data = response.json()
        content = data.get('response', '')
        
        print("\n📥 收到的回复：")
        print("-" * 60)
        print(content)
        print("-" * 60)
        
        # 格式检查
        print("\n🔍 格式检查结果：")
        
        # 检查1: 代码块闭合后是否有空行
        code_block_pattern = r'```\n###'
        violations = re.findall(r'```\n###', content)
        if violations:
            print(f"❌ 发现问题：{len(violations)} 处代码块后直接紧跟标题，缺少空行")
        else:
            print("✅ 代码块后有空行分隔")
        
        # 检查2: 标题格式
        headers = re.findall(r'###\s+\w+', content)
        print(f"📊 找到 {len(headers)} 个标题: {headers}")
        
        # 检查3: 代码块数量
        code_blocks = re.findall(r'```(\w+)', content)
        print(f"📊 找到 {len(code_blocks)} 个代码块: {code_blocks}")
        
        # 显示原始字符以便调试
        print("\n🔬 原始字符检查（前500字符）：")
        raw = repr(content[:500])
        print(raw)
        
    except requests.exceptions.ConnectionError:
        print(f"❌ 无法连接到 {API_URL}")
        print("请检查：1. 后端是否运行  2. API_URL是否正确")
    except Exception as e:
        print(f"❌ 错误: {e}")

if __name__ == "__main__":
    test_ooo()
