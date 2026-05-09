#!/bin/bash
# 最小验证脚本，改完代码跑这个

set -e
echo "🚀 开始验证..."

# 1. 语法检查
python -m py_compile server.py
echo "✅ Python 语法 OK"

# 2. 起服务
pkill -f "python server.py" 2>/dev/null || true
sleep 1
python server.py &
SERVER_PID=$!
sleep 3

# 3. 健康检查 + 关键接口

for path in "/health" "/admin/configs" "/admin/users"; do
    code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000$path")
    if [ "$code" = "404" ]; then
        echo "❌ $path -> 404 (路由丢失)"
        exit 1
    fi
    echo "✅ $path -> $code"
done

# 4. 前端 fetch 路径都能找到
echo "🚀 检查前端调用的接口..."
grep -rhoP 'fetch\(["'"'"'][^"'"'"']+' pages/*.html 2>/dev/null \
  | sed 's/fetch(["'"'"']//g' | sort -u | while IFS= read -r path; do
    [[ "$path" =~ ^http ]] && continue
    [[ -z "$path" ]] && continue
    code=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:8000${path}")
    if [ "$code" = "404" ]; then
        echo "❌ 前端调用 $path 但后端 404!"
        exit 1
    fi
    echo "  ✅ $path -> $code"
done

echo ""
echo "🎉 全部验证通过"
