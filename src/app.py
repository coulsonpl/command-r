import os  # 导入os模块以访问环境变量
import json
import re
import time
import asyncio
from aiohttp import ClientSession, web
from dotenv import load_dotenv
import logging

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 从环境变量读取代理设置
http_proxy = os.getenv('HTTP_PROXY')
https_proxy = os.getenv('HTTPS_PROXY')

async def fetch(req):
    if req.method == "OPTIONS":
        return web.Response(body="", headers={'Access-Control-Allow-Origin': '*', 'Access-Control-Allow-Headers': '*'}, status=204)

    body = await req.json()
    # logging.info(f"Request body: {json.dumps(body, indent=4)}")

    data = {"chat_history": []}
    try:
        for i in range(len(body["messages"]) - 1):
            data["chat_history"].append({"role": "CHATBOT" if body["messages"][i]["role"] == "assistant" else body["messages"][i]["role"].upper(),
                                         "message": body["messages"][i]["content"]})
        data["message"] = body["messages"][-1]["content"]
    except Exception as e:
        return web.Response(text=str(e))

    data["stream"] = body.get("stream", False)

    if body["model"].startswith("net-"):
        data["connectors"] = [{"id": "web-search"}]
    for key, value in body.items():
        if not re.match(r"^(model|messages|stream)", key, re.IGNORECASE):
            data[key] = value
    if re.match(r"^(net-)?command", body["model"]):
        data["model"] = body["model"].replace("net-", "")
    if not data.get("model"):
        data["model"] = "command-r"

    headers = {'content-type': 'application/json'}
    if req.headers.get('authorization'):
        headers["Authorization"] = req.headers.get('authorization')
    else:
        url_params = req.url.query
        headers["Authorization"] = "bearer " + url_params.get('key')

    async with ClientSession(trust_env=True) as session:  # trust_env=True允许从环境变量读取代理配置
        async with session.post('https://api.cohere.ai/v1/chat', json=data, headers=headers, proxy=http_proxy or https_proxy) as resp:
            if resp.status != 200:
                response_text = await resp.text()
                logging.info(f"Request headers: {json.dumps(headers, indent=4)}")
                logging.info(f"Response status: {resp.status} Response body: {json.dumps(headers, indent=4)}")
                return resp

            if not data["stream"]:
                response_json = await resp.json()
                wrapped_chunk = {
                    "id": "chatcmpl-9FLdP4Hj7KJ2BYYeskHyLALXnLzrY",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": data["model"],
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "content": response_json.get("text", response_json.get("error"))
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0
                    },
                    "system_fingerprint": None
                }
                return web.Response(text=json.dumps(wrapped_chunk, ensure_ascii=False), content_type='application/json', headers={
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Allow-Headers': '*',
                })
            else:
                # 流式返回
                async def stream_response(resp):
                    writer = web.StreamResponse()
                    writer.headers['Access-Control-Allow-Origin'] = '*'
                    writer.headers['Access-Control-Allow-Headers'] = '*'
                    writer.headers['Content-Type'] = 'text/event-stream; charset=UTF-8'
                    await writer.prepare(req)

                    async for chunk in resp.content.iter_any():
                        try:
                            chunk_str = chunk.decode('utf-8')
                        except UnicodeDecodeError:
                            try:
                                chunk_str = chunk.decode('latin-1')
                            except UnicodeDecodeError:
                                chunk_str = chunk.decode('gbk', errors='ignore')  # 忽略无法解码的字符

                        try:
                            chunk_json = json.loads(chunk_str)
                        except json.JSONDecodeError as e:
                            logging.info(f"Failed to parse JSON chunk: {e}")
                            continue

                        wrapped_chunk = {
                            "id": "chatcmpl-9FLdP4Hj7KJ2BYYeskHyLALXnLzrY",
                            "object": "chat.completion.chunk",
                            "created": int(time.time()),
                            "model": data["model"],
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "role": "assistant",
                                        "content": chunk_json.get("text", chunk_json.get("error"))
                                    },
                                    "finish_reason": "stop",
                                }
                            ],
                            "usage": {
                                "prompt_tokens": 0,
                                "completion_tokens": 0,
                                "total_tokens": 0
                            },
                            "system_fingerprint": None
                        }

                        event_data = f"data: {json.dumps(wrapped_chunk, ensure_ascii=False)}\n\n"
                        await writer.write(event_data.encode('utf-8'))

                    return writer

                return await stream_response(resp)

async def onRequest(request):
    return await fetch(request)

async def sleep(ms):
    await asyncio.sleep(ms / 1000)

app = web.Application()
app.router.add_route("*", "/v1/chat/completions", onRequest)

if __name__ == '__main__':
    port = int(os.getenv('SERVER_PORT', 3030))  # 从环境变量
    web.run_app(app, host='0.0.0.0', port=port)