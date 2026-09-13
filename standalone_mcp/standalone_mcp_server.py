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
import os
import subprocess
import sys
import threading
import time
import traceback

# Codificacion real de la consola: en Windows cmd usa la pagina OEM (cp850
# aqui), no la ANSI que Python asume por defecto -- por eso las enyes y
# tildes llegaban rotas.
_CONSOLE_ENC = "oem" if os.name == "nt" else "utf-8"

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

class _ThreadStream:
    """sys.stdout/stderr compartido, con un buffer por hilo.

    redirect_stdout() no sirve aqui: cambia sys.stdout para TODO el
    proceso, asi que un hilo que se queda colgado tras un timeout nunca
    sale del context manager y se traga la salida de las llamadas
    siguientes. Esto aisla cada ejecucion a su propio hilo.
    """

    def __init__(self, real):
        self._real = real
        self._buffers = {}

    def register(self, buf):
        self._buffers[threading.get_ident()] = buf

    def unregister(self):
        self._buffers.pop(threading.get_ident(), None)

    def write(self, s):
        buf = self._buffers.get(threading.get_ident())
        if buf is not None:
            return buf.write(s)
        return self._real.write(s) if self._real else len(s)

    def flush(self):
        if self._real:
            self._real.flush()

    def isatty(self):
        return False


_stdout_mux = _ThreadStream(sys.stdout)
_stderr_mux = _ThreadStream(sys.stderr)
sys.stdout = _stdout_mux
sys.stderr = _stderr_mux


# Persiste variables entre llamadas a run_python, igual que td_code
_globals_ns = {}

# Ejecuciones que agotaron su timeout y siguen vivas. Python no permite
# matar un hilo, asi que lo unico honesto es avisar de que ese codigo
# sigue corriendo y puede seguir tocando _globals_ns.
_runaway = []


def _clean_traceback():
    """Traceback recortado: empieza en el codigo del usuario, sin los
    frames internos del servidor."""
    lines = traceback.format_exc().splitlines(keepends=True)
    for i, line in enumerate(lines):
        if 'File "<run_python>"' in line:
            return "Traceback (most recent call last):\n" + "".join(lines[i:])
    return "".join(lines)


def _compose(out, err, extra=""):
    """Salida final: SIEMPRE lo que se llego a imprimir, aunque despues
    petara o se agotara el timeout."""
    parts = []
    if out.getvalue():
        parts.append(out.getvalue().rstrip("\n"))
    if err.getvalue():
        parts.append("[stderr]\n" + err.getvalue().rstrip("\n"))
    if extra:
        parts.append(extra)
    return "\n".join(parts) if parts else "(sin salida)"


async def _tool_run_python(args):
    code = args.get("code", "")
    timeout = float(args.get("timeout", 30))
    out, err = io.StringIO(), io.StringIO()
    box = {}

    _runaway[:] = [t for t in _runaway if t.is_alive()]
    aviso = ""
    if _runaway:
        aviso = ("[aviso] %d ejecucion(es) anterior(es) agotaron su timeout y "
                 "siguen vivas en segundo plano; pueden estar modificando las "
                 "variables compartidas.\n" % len(_runaway))

    done = threading.Event()

    def _run():
        _stdout_mux.register(out)
        _stderr_mux.register(err)
        try:
            exec(compile(code, "<run_python>", "exec"), _globals_ns)
        except BaseException:
            box["tb"] = _clean_traceback()
        finally:
            _stdout_mux.unregister()
            _stderr_mux.unregister()
            done.set()

    # Hilo propio y daemon: uno colgado no impide cerrar el servidor.
    # Los de asyncio.to_thread si lo impiden (se esperan al salir).
    th = threading.Thread(target=_run, daemon=True, name="run_python")
    th.start()

    limite = time.monotonic() + timeout
    while not done.is_set() and time.monotonic() < limite:
        await asyncio.sleep(0.02)

    if done.is_set():
        return aviso + _compose(out, err, box.get("tb", ""))

    _runaway.append(th)
    return aviso + _compose(out, err, (
        "[error] timeout tras %ss -- el codigo SIGUE ejecutandose en segundo "
        "plano (Python no puede matar un hilo). Arriba tienes lo que llego a "
        "imprimir. Si se ha quedado colgado, reinicia el servidor." % timeout))


async def _tool_run_command(args):
    command = args.get("command", "")
    timeout = float(args.get("timeout", 30))
    try:
        proc = await asyncio.to_thread(
            subprocess.run, command, shell=True, capture_output=True,
            encoding=_CONSOLE_ENC, errors="replace", timeout=timeout,
        )
        return f"[returncode] {proc.returncode}\n[stdout]\n{proc.stdout}\n[stderr]\n{proc.stderr}"
    except subprocess.TimeoutExpired as e:
        return (f"[error] timeout tras {timeout}s (proceso terminado)\n"
                f"[stdout parcial]\n{e.stdout or ''}\n[stderr parcial]\n{e.stderr or ''}")
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
