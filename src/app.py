import os
import json
import re
import time
from aiohttp import ClientSession, web
from dotenv import load_dotenv
import logging
import threading

# 加载环境变量
load_dotenv()

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# 获取环境变量值，支持大小写不敏感，空值返回默认值。
def get_env_value(key, default=None):
    value = os.getenv(key) or os.getenv(key.lower()) or os.getenv(key.upper())
    return default if value in [None, ''] else value

# 从环境变量读取代理设置（支持大小写）
http_proxy = get_env_value('HTTP_PROXY')
https_proxy = get_env_value('HTTPS_PROXY')

# 初始化全局变量和锁
last_key_index = -1
index_lock = threading.Lock()

async def fetch(req):
    if req.method == "OPTIONS":
        return create_options_response()

    try:
        body = await req.json()
        data = prepare_data(body)
        headers = prepare_headers(req)
        # logging.info(f"Request headers: {headers}")
        response = await post_request(data, headers, req)
        return response
    except Exception as e:
        logging.error(f"Error processing request: {str(e)}")
        return web.Response(text=str(e), status=500)

def create_options_response():
    return web.Response(body="", headers={
        'Access-Control-Allow-Origin': '*', 
        'Access-Control-Allow-Headers': '*'
    }, status=204)

def prepare_data(body):
    data = {"chat_history": [], "stream": body.get("stream", False)}
    for message in body.get("messages", [])[:-1]:
        data["chat_history"].append({
            "role": "CHATBOT" if message["role"] == "assistant" else message["role"].upper(),
            "message": message["content"]
        })
    data.update({k: v for k, v in body.items() if not re.match(r"^(model|messages|stream)$", k, re.IGNORECASE)})
    data["message"] = body["messages"][-1]["content"] if body.get("messages") else ""
    data["model"] = body.get("model", "").replace("net-", "") or "command-r"
    return data

def prepare_headers(req):
    headers = {'Content-Type': 'application/json; charset=utf-8', 'Accept': '*/*', 'Accept-Encoding': 'gzip, deflate, br, zstd', 'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36'}
    authorization = req.headers.get('authorization')
    if authorization and authorization.lower().startswith('bearer '):
        global last_key_index
        with index_lock:
            keys = authorization[7:].split(',')
            last_key_index = (last_key_index + 1) % len(keys) if keys else 0
            authorization = f"Bearer {keys[last_key_index]}"
        headers["Authorization"] = authorization
    else:
        url_params = req.url.query
        headers["Authorization"] = "Bearer " + url_params.get('key', '')
    return headers

async def post_request(data, headers, req):
    async with ClientSession(trust_env=True) as session:
        return await send_request(session, data, headers, req)

async def send_request(session, data, headers, req):
    async with session.post('https://api.cohere.ai/v1/chat', json=data, headers=headers, proxy=http_proxy or https_proxy) as resp:
        if resp.status != 200:
            response_text = await resp.text()
            logging.error(f"Error from API: Status: {resp.status}, Body: {response_text}")
            return resp
        return await handle_response(data, resp, req)

async def handle_response(data, resp, req):
    if not data["stream"]:
        response_json = await resp.json()
        return create_response(data, response_json)
    else:
        return await stream_response(resp, data, req)

def create_response(data, response_json):
    wrapped_chunk = generate_wrapped_chunk(data, response_json.get("text", response_json.get("error")))
    return web.Response(text=json.dumps(wrapped_chunk, ensure_ascii=False), content_type='application/json', headers={
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Headers': '*',
    })

def generate_wrapped_chunk(data, content):
    return {
        "id": "chatcmpl-9FLdP4Hj7KJ2BYYeskHyLALXnLzrY",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": data["model"],
        "choices": [
            {
                "index": 0,
                "delta": {
                    "role": "assistant",
                    "content": content
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

async def stream_response(resp, data, req):
    writer = web.StreamResponse()
    writer.headers['Access-Control-Allow-Origin'] = '*'
    writer.headers['Access-Control-Allow-Headers'] = '*'
    writer.headers['Content-Type'] = 'text/event-stream; charset=UTF-8'
    await writer.prepare(req)

    async for chunk in resp.content.iter_any():
        try:
            chunk_json = json.loads(chunk.decode('utf-8'))
            wrapped_chunk = generate_wrapped_chunk(data, chunk_json.get("text", chunk_json.get("error")))
            event_data = f"data: {json.dumps(wrapped_chunk, ensure_ascii=False)}\n\n"
            await writer.write(event_data.encode('utf-8'))
        except Exception as e:
            logging.error(f"Error streaming response: {e}")
            break

    return writer

async def onRequest(request):
    return await fetch(request)

app = web.Application()
app.router.add_route("*", "/v1/chat/completions", onRequest)

if __name__ == '__main__':
    port = int(get_env_value('SERVER_PORT', 3030))
    web.run_app(app, host='0.0.0.0', port=port)