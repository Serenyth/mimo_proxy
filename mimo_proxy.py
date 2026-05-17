"""
MiMo API 代理 - 修复 content: null 兼容性问题

使用方法：
1. 安装依赖：pip install fastapi uvicorn httpx
2. 运行代理：python mimo_proxy.py
3. 修改 openclaw.json 中的 baseUrl 为 http://localhost:8000/v1
"""

import subprocess
import sys
import platform
import json
from datetime import datetime
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
import httpx
import uvicorn

app = FastAPI(title="MiMo Compatibility Proxy")

MIMO_API_BASE = "https://token-plan-cn.xiaomimimo.com"
PROXY_PORT = 16413


def kill_process_on_port(port):
    """检测并释放指定端口的进程"""
    print(f"[CHECK] Checking port {port}...")

    if platform.system() == "Windows":
        try:
            result = subprocess.run(
                ['netstat', '-ano'],
                capture_output=True,
                text=True,
                encoding='gbk'
            )

            lines = result.stdout.split('\n')
            pids = set()

            for line in lines:
                if f':{port}' in line and 'LISTENING' in line:
                    parts = line.split()
                    if len(parts) >= 5:
                        pid = parts[-1]
                        pids.add(pid)

            if not pids:
                print(f"[OK] Port {port} is available")
                return True

            print(f"[WARN] Port {port} is occupied by PIDs: {', '.join(pids)}")

            for pid in pids:
                try:
                    print(f"[KILL] Terminating process {pid}...")
                    subprocess.run(['taskkill', '/F', '/PID', pid],
                                 capture_output=True, check=True)
                    print(f"[OK] Process {pid} terminated")
                except subprocess.CalledProcessError as e:
                    print(f"[ERROR] Failed to kill process {pid}: {e}")
                    return False

            import time
            time.sleep(1)

            result = subprocess.run(
                ['netstat', '-ano'],
                capture_output=True,
                text=True,
                encoding='gbk'
            )

            still_occupied = any(
                f':{port}' in line and 'LISTENING' in line
                for line in result.stdout.split('\n')
            )

            if still_occupied:
                print(f"[ERROR] Port {port} is still occupied")
                return False
            
            print(f"[OK] Port {port} released successfully")
            return True
            
        except Exception as e:
            print(f"[ERROR] Failed to check/kill process: {e}")
            return False
    
    else:
        import socket
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                result = s.connect_ex(('127.0.0.1', port))
                if result != 0:
                    print(f"[OK] Port {port} is available (not in use)")
                    return True
            print(f"[WARN] Port {port} is occupied, attempting to release...")
            subprocess.run(['fuser', '-k', f'{port}/tcp'],
                         capture_output=True)
            import time
            time.sleep(1)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                result = s.connect_ex(('127.0.0.1', port))
                if result != 0:
                    print(f"[OK] Port {port} released successfully")
                    return True
                print(f"[ERROR] Port {port} is still occupied")
                return False
        except Exception as e:
            print(f"[ERROR] Failed to check/release port: {e}")
            return False


def log_request_info(body, modified_body):
    """记录请求详细信息用于调试"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    print("\n" + "=" * 80)
    print(f"[REQUEST] {timestamp}")
    print("=" * 80)
    
    # 记录基本信息
    model = body.get("model", "unknown")
    messages_count = len(body.get("messages", []))
    has_tools = "tools" in body and len(body.get("tools", [])) > 0
    
    print(f"Model: {model}")
    print(f"Messages count: {messages_count}")
    print(f"Has tools: {has_tools}")
    
    # 检查并显示所有 assistant 消息中的问题字段
    if "messages" in modified_body:
        print("\n[MESSAGES ANALYSIS]:")
        for i, msg in enumerate(modified_body["messages"]):
            role = msg.get("role", "unknown")
            content = msg.get("content")
            has_tool_calls = "tool_calls" in msg and msg.get("tool_calls")
            has_reasoning = "reasoning_content" in msg
            
            if role == "assistant":
                issues = []
                if content is None and has_tool_calls:
                    issues.append("content: null (with tool_calls)")
                elif content == "" and has_tool_calls:
                    issues.append("content: '' (FIXED)")
                
                if not has_reasoning and has_tool_calls:
                    issues.append("missing reasoning_content")
                
                status = " | ".join(issues) if issues else "OK"
                print(f"  [{i}] role={role}, tool_calls={has_tool_calls}, "
                      f"content={repr(content)}, reasoning={has_reasoning} -> {status}")
    
    # 显示修改的字段
    changes = []
    original_msgs = body.get("messages", [])
    modified_msgs = modified_body.get("messages", [])
    
    for orig, mod in zip(original_msgs, modified_msgs):
        if orig.get("content") != mod.get("content"):
            changes.append(
                f"Message {original_msgs.index(orig)}: "
                f"content {repr(orig.get('content'))} -> {repr(mod.get('content'))}"
            )
    
    if changes:
        print("\n[CHANGES MADE]:")
        for change in changes:
            print(f"  - {change}")
    
    print("=" * 80 + "\n")


@app.api_route("/v1/chat/completions", methods=["POST"])
async def proxy_chat_completions(request: Request):
    try:
        body = await request.json()
        
        # 创建副本用于修改（保留原始数据用于日志）
        modified_body = json.loads(json.dumps(body))
        
        # 修复 assistant messages 中的各种兼容性问题
        if "messages" in modified_body:
            for msg in modified_body["messages"]:
                if msg.get("role") == "assistant":
                    # 问题1：修复 content: null（当有 tool_calls 时）
                    if msg.get("tool_calls") and msg.get("content") is None:
                        msg["content"] = ""
                    
                    # 问题2：确保有 reasoning_content 字段（MiMo要求）
                    if ("reasoning_content" not in msg 
                        and msg.get("tool_calls")):
                        msg["reasoning_content"] = ""
                    
                    # 问题3：检查 tool_calls 格式
                    if "tool_calls" in msg and msg["tool_calls"]:
                        for tc in msg["tool_calls"]:
                            # 确保 type 字段存在
                            if "type" not in tc:
                                tc["type"] = "function"
                            
                            # 确保函数参数格式正确
                            if "function" in tc:
                                func = tc["function"]
                                # 如果 arguments 是字符串，确保是合法JSON
                                if "arguments" in func:
                                    try:
                                        json.loads(func["arguments"])
                                    except:
                                        func["arguments"] = "{}"
        
        # 记录请求信息（调试用）
        log_request_info(body, modified_body)
        
        # 转发请求头（排除 host）
        headers = dict(request.headers)
        headers.pop("host", None)
        headers.pop("content-length", None)
        
        # 转发到 MiMo API
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(
                f"{MIMO_API_BASE}/v1/chat/completions",
                json=modified_body,
                headers=headers,
            )
        
        # 记录响应信息
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        if response.status_code != 200:
            print("\n" + "=" * 80)
            print(f"[RESPONSE ERROR] {timestamp}")
            print("=" * 80)
            print(f"Status Code: {response.status_code}")
            try:
                error_detail = response.json()
                print(f"Error Detail:")
                print(json.dumps(error_detail, indent=2, ensure_ascii=False))
            except:
                print(f"Response Body: {response.text[:500]}")
            print("=" * 80 + "\n")
        
        return Response(
            content=response.content,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type="application/json",
        )
        
    except Exception as e:
        print(f"\n[PROXY ERROR] {str(e)}\n")
        return JSONResponse(
            status_code=500,
            content={"error": f"Proxy error: {str(e)}"},
        )


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "mimo-proxy"}


if __name__ == "__main__":
    print("=" * 60)
    print("[MiMo] Compatibility Proxy starting...")
    print("=" * 60)
    
    if not kill_process_on_port(PROXY_PORT):
        print(f"\n[FATAL] Cannot release port {PROXY_PORT}")
        print("Please manually close the application using this port or change PROXY_PORT")
        sys.exit(1)
    
    print("[INFO] Function: Auto-fix content:null issue for tool_calls")
    print(f"[INFO] Listening on: http://localhost:{PROXY_PORT}")
    print("[NOTE] Please modify openclaw.json:")
    print(f'   "baseUrl": "http://localhost:{PROXY_PORT}/v1"')
    print("[DEBUG] Detailed logging enabled - check console for request/response details")
    print("=" * 60)
    
    uvicorn.run(app, host="127.0.0.1", port=PROXY_PORT)
