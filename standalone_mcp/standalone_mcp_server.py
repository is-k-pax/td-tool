"""
standalone_mcp_server.py
Servidor MCP (Streamable HTTP, JSON-RPC 2.0) que expone ejecucion de Python /
comandos de shell en esta maquina -- SIN necesidad de tener TouchDesigner abierto.

Imita el mismo protocolo JSON-RPC que usa td_mcp_adapter dentro de td_tool, asi
que encaja en el mismo setup de Tailscale + mcp-remote, pero corre como proceso
standalone (ej. lanzado por un .bat o Task Scheduler al iniciar sesion).

Uso:
    python standalone_mcp_server.py --port 18777

Tools expuestas:
    run_python(code, timeout=30)     - ejecuta Python, captura stdout/stderr
    run_command(command, timeout=30) - ejecuta un comando de shell
    read_file(path)                  - devuelve el contenido de un archivo
    write_file(path, content)        - escribe/sobreescribe un archivo
"""

import argparse
import asyncio
import io
import subprocess
import traceback
from contextlib import redirect_stdout, redirect_stderr

from aiohttp import web

TOOLS = [
    {
        "name": "run_python",
        "description": "Ejecuta codigo Python en esta maquina y devuelve stdout/stderr. No requiere TouchDesigner.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "timeout": {"type": "number", "default": 30},
            },
            "required": ["code"],
        },
    },
    {
        "name": "run_command",
        "description": "Ejecuta un comando de shell en esta maquina y devuelve stdout/stderr/returncode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "number", "default": 30},
            },
            "required": ["command"],
        },
    },
    {
        "name": "read_file",
        "description": "Lee y devuelve el contenido de un archivo de texto en esta maquina.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Escribe (sobreescribe) un archivo de texto en esta maquina.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
]

# Persiste variables entre llamadas a run_python, igual que td_code
_globals_ns = {}


async def _tool_run_python(args):
    code = args.get("code", "")
    timeout = float(args.get("timeout", 30))
    out, err = io.StringIO(), io.StringIO()

    def _exec():
        with redirect_stdout(out), redirect_stderr(err):
            exec(compile(code, "<run_python>", "exec"), _globals_ns)

    try:
        await asyncio.wait_for(asyncio.to_thread(_exec), timeout=timeout)
        result = out.getvalue()
        if err.getvalue():
            result += "\n[stderr]\n" + err.getvalue()
        return result or "(sin salida)"
    except asyncio.TimeoutError:
        return f"[error] timeout tras {timeout}s"
    except Exception:
        return "[error]\n" + traceback.format_exc()


async def _tool_run_command(args):
    command = args.get("command", "")
    timeout = float(args.get("timeout", 30))
    try:
        proc = await asyncio.to_thread(
            subprocess.run, command, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return f"[returncode] {proc.returncode}\n[stdout]\n{proc.stdout}\n[stderr]\n{proc.stderr}"
    except Exception:
        return "[error]\n" + traceback.format_exc()


async def _tool_read_file(args):
    try:
        with open(args["path"], "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return "[error]\n" + traceback.format_exc()


async def _tool_write_file(args):
    try:
        with open(args["path"], "w", encoding="utf-8") as f:
            f.write(args.get("content", ""))
        return f"OK: escrito {args['path']}"
    except Exception:
        return "[error]\n" + traceback.format_exc()


TOOL_IMPL = {
    "run_python": _tool_run_python,
    "run_command": _tool_run_command,
    "read_file": _tool_read_file,
    "write_file": _tool_write_file,
}


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


async def handle_message(msg):
    if not isinstance(msg, dict):
        return _error(None, -32600, "Invalid Request")

    method = msg.get("method")
    params = msg.get("params", {}) or {}
    request_id = msg.get("id")
    is_notification = "id" not in msg

    if method == "initialize":
        return _result(request_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "standalone-python-exec", "version": "1.0.0"},
        })

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})

    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments", {}) or {}
        impl = TOOL_IMPL.get(name)
        if impl is None:
            return _error(request_id, -32601, f"Tool desconocida: {name}")
        text = await impl(args)
        return _result(request_id, {"content": [{"type": "text", "text": text}]})

    if is_notification:
        return None
    return _error(request_id, -32601, f"Method not found: {method}")


async def handle_mcp_post(request: web.Request) -> web.Response:
    try:
        data = await request.json()
    except Exception as e:
        return web.json_response(_error(None, -32700, f"Parse error: {e}"), status=400)

    if isinstance(data, list):
        responses = []
        for m in data:
            r = await handle_message(m)
            if r is not None:
                responses.append(r)
        if not responses:
            return web.Response(status=202)
        return web.json_response(responses)

    r = await handle_message(data)
    if r is None:
        return web.Response(status=202)
    return web.json_response(r)


def build_app():
    app = web.Application()
    app.router.add_post("/mcp", handle_mcp_post)

    @web.middleware
    async def cors(request, handler):
        resp = await handler(request)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp

    app.middlewares.append(cors)
    return app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18777)
    args = parser.parse_args()

    app = build_app()
    print(f"[standalone-mcp] Escuchando en http://{args.host}:{args.port}/mcp")
    web.run_app(app, host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
