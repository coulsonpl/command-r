import os
import json
import re
import time
from aiohttp import ClientSession, web
from dotenv import load_dotenv
import logging
import threading
import chardet

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
    logging.error(f"prepare_data body: {body}")
    data = {"chat_history": [], "stream": body.get("stream", False)}
    for message in body.get("messages", [])[:-1]:
        data["chat_history"].append({
            "role": "CHATBOT" if message["role"] == "assistant" else message["role"].upper(),
            "message": message["content"]
        })
    data['message'] = body['messages'][-1].get('content')
    if str(body.get('model', '')).startswith("net-"):
        data['connectors'] = [{"id": "web-search"}]
    for key, value in body.items():
        if not re.match(r'^(model|messages|stream)$', key, re.I):
            data[key] = value
    if re.match(r'^(net-)?command', str(body.get('model', ''))):
        data['model'] = re.sub(r'^net-', '', str(body.get('model', '')))
    if 'model' not in data or not data['model']:
        data['model'] = "command-r"
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

# 尝试解析多个JSON字符串拼在一起的字符串
def extract_and_concatenate_texts(json_string):
    concatenated_text = ""
    start_index = 0
    for end_index in range(len(json_string)):
        # 仅当检测到闭合大括号时尝试解析
        if json_string[end_index] == '}':
            try:
                # 尝试解析当前子字符串为JSON
                json_obj = json.loads(json_string[start_index:end_index + 1])
                # 如果成功，提取"text"字段并拼接
                if 'text' in json_obj:
                    concatenated_text += json_obj['text']
                # 更新开始索引为下一个字符的位置
                start_index = end_index + 1
            except json.JSONDecodeError:
                # 如果解析失败，忽略错误继续尝试
                pass
    return concatenated_text

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
    wrapped_chunk = {
        "id": "chatcmpl-QXlha2FBbmROaXhpZUFyZUF3ZXNvbWUK",
        "object": "chat.completion",
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

async def stream_response(resp, data, req):
    created = int(time.time())

    writer = web.StreamResponse()
    writer.headers['Access-Control-Allow-Origin'] = '*'
    writer.headers['Access-Control-Allow-Headers'] = '*'
    writer.headers['Content-Type'] = 'text/event-stream; charset=UTF-8'
    await writer.prepare(req)

    async for chunk in resp.content.iter_any():
        # 字符串解码
        encodings =  ['utf-8', 'latin-1', 'gbk', 'ascii', 'utf-16', 'gb2312', 'gb18030', 'big5', 'euc-jp', 'euc-kr', 'shift_jis', 'iso-8859-1', 'iso-8859-15', 'windows-1252', 'koi8-r', 'mac_cyrillic', 'utf-32']
        for encoding in encodings:
            try:
                chunk_str = chunk.decode(encoding)
                break
            except UnicodeDecodeError:
                pass
        if chunk_str == None:
            encoding = chardet.detect(chunk)
            chunk_str = chunk.decode(encoding['encoding'], errors='ignore') # 忽略无法解码的字符
        
        # 解析JSON字符串
        try:
            chunk_json = json.loads(chunk_str)
            content_text = chunk_json.get("text")
        except Exception as e:
            # 尝试解析多个JSON字符串拼在一起的字符串
            content_text = extract_and_concatenate_texts(chunk_str)
            # logging.error(f"Failed to parse JSON chunk: {e}")

        # 流式回复
        if content_text is not None and content_text != "":
            wrapped_chunk = { 
                "id": "chatcmpl-QXlha2FBbmROaXhpZUFyZUF3ZXNvbWUK", "object": "chat.completion.chunk", 
                "created": created, 
                "model": data["model"], 
                "choices": [
                    { "index": 0, "delta": { "role": "assistant", "content": content_text }, "finish_reason": None }
                ]
            }
            event_data = f"data: {json.dumps(wrapped_chunk, ensure_ascii=False)}\n\n"
            await writer.write(event_data.encode('utf-8'))

    # finish chunk
    finish_wrapped_chunk = { 
        "id": "chatcmpl-QXlha2FBbmROaXhpZUFyZUF3ZXNvbWUK", "object": "chat.completion.chunk", 
        "created": created, 
        "model": data["model"], 
        "choices": [
            { "index": 0, "delta": {}, "finish_reason": 'stop' }
        ]
    }
    finish_event_data = f"data: {json.dumps(finish_wrapped_chunk, ensure_ascii=False)}\n\n"
    await writer.write(finish_event_data.encode('utf-8'))

    return writer

async def onRequest(request):
    return await fetch(request)

app = web.Application()
app.router.add_route("*", "/v1/chat/completions", onRequest)

if __name__ == '__main__':
    port = int(get_env_value('SERVER_PORT', 3030))
    web.run_app(app, host='0.0.0.0', port=port)