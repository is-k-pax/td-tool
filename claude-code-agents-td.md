# Agentes de Claude Code dentro de TouchDesigner

Guía de Framemov para montar un **agente de Claude Code** dentro de un proyecto de TouchDesigner: un Claude que vive dentro del `.toe`, recibe órdenes (voz desde una app, texto, botones…), actúa sobre la instalación con herramientas MCP y contesta (voz, app, TD).

Escrita para dársela a un chat nuevo de Claude: *"Lee esta guía y móntame el agente para la instalación X"*. Recoge la arquitectura, el código de referencia y los problemas que ya hemos resuelto, para no volver a tropezar con ellos.

Implementaciones de referencia:
- **ABAC** (mesa cinética, `/project1/ui/claude_code_bridge` + `/project1/abac_tools`): voz desde el iPad, contesta llamando a una herramienta (`voz.responder`).
- **Frameroom** (sala inmersiva, `/project1/LOPS/claude_agent` + `/project1/LOPS/frameroom_tools`): sustituto directo de un Agent LOP, contesta por TTS y en el iPad.
- **Agente Claude conversacional voz** (framemov-14, `/claude_voz`): `claude_agent` + `voz_app` (app de móvil con STT y WebSocket) + `voz_tts` (Kokoro); la voz suena en el móvil (§7b).
- **Claude TD** (`~/Claude/claude-td`): no es un agente dentro de TD, sino una app de escritorio para desarrollar proyectos con Claude Code (ver el final).

---

## 1. Por qué así

| | Claude Desktop | Agent LOP (dotsimulate) | **Claude Code dentro de TD** |
|---|---|---|---|
| Cómo llega el mensaje | Pegar en la ventana (SendKeys / UI Automation), frágil | Parámetro `Prompt` + `Call` | stdin JSON a un proceso propio |
| Primera respuesta | — | Lenta (carga ChatTD) | Proceso siempre abierto: 3–6 s típicos |
| Pago | Suscripción | API (por uso) | **Suscripción** (login de Claude Code) |
| Fin de respuesta | Adivinar leyendo la pantalla | Callback | Evento `result` exacto |
| Arranque con el `.toe` | Manual | Problemático | Automático (`onStart`) |
| Herramientas | MCP td-tool | Tool COMPs de LOPs | MCP: td-tool y/o un tool manager propio |

Claude Code en modo headless (`claude -p --input-format stream-json --output-format stream-json`) se queda abierto, mantiene la conversación, habla con los servidores MCP directamente y usa la cuenta con la que se hizo login.

---

## 2. Arquitectura

```
Entrada (app/voz/botón)
   └─► COMP agente  ── Prompt + Call (pulse)   o   stt_text + stt_trigger
         │  cc.send(texto)  → stdin: {"type":"user","message":{...}}\n
         ▼
   claude.exe -p --input-format stream-json --output-format stream-json --verbose
         --strict-mcp-config --mcp-config {...}  --allowedTools ...  --append-system-prompt ...
         cwd = project.folder   (lee CLAUDE.md de la carpeta del proyecto)
         │  llama herramientas MCP ──► tool manager(s) dentro del mismo TD (HTTP 127.0.0.1)
         ▼
   stdout (una línea JSON por evento) → hilo lector → queue
         │
   frame_exec.onFrameStart → cc.poll() (hilo principal de TD)
         ▼
   evento 'result' → DAT respuesta + callbacks.onAnswer() → TTS / app / lo que toque
```

Puntos clave:
- **Nada bloquea el hilo principal de TD.** Las herramientas MCP del td-tool se ejecutan en el hilo de TD; si TD espera a Claude de forma síncrona, se bloquea a sí mismo (deadlock). Por eso: hilo lector + cola + `poll()` cada frame.
- **Un único proceso por agente**, guardado en `sys._cc_agents[path]` (sobrevive a recompilar el módulo).
- **`--strict-mcp-config`** con la config inline: carga solo los servidores que digamos (ni los de `claude mcp add`, ni los conectores de claude.ai, que lo hacen más lento). Además el agente no depende de la configuración de cada PC.
- **`ENABLE_TOOL_SEARCH=false`** en el entorno del proceso: las herramientas MCP quedan cargadas desde el inicio (si no, gasta una vuelta en "buscar" la herramienta).
- **`cwd = project.folder`**: Claude Code lee solo el `CLAUDE.md` de esa carpeta.

---

## 3. Requisitos en cada PC (una vez)

En PowerShell (no hace falta administrador):

```powershell
irm https://claude.ai/install.ps1 | iex
[Environment]::SetEnvironmentVariable("Path", [Environment]::GetEnvironmentVariable("Path","User") + ";$env:USERPROFILE\.local\bin", "User")
```

Cerrar y abrir PowerShell, y luego:

```powershell
claude          # dentro: /login con la cuenta de SUSCRIPCIÓN (no Console) → /exit
echo $env:ANTHROPIC_API_KEY   # debe salir vacío; si existe, Claude Code cobra por API
```

- Claude Code queda en `%USERPROFILE%\.local\bin\claude.exe`. **TD no ve ese PATH** (aunque lo añadas, hasta reiniciar TD); el agente busca el ejecutable por ruta completa.
- **Instalación desatendida** (PC que puede pasar semanas apagado): `claude setup-token` genera un token de ~1 año; guardarlo en la variable de usuario `CLAUDE_CODE_OAUTH_TOKEN` y reiniciar TD. Ojo al copiarlo: se trunca fácilmente; si da `401 OAuth access token is invalid`, quitar la variable (si no, bloquea el login normal) y repetir, o usar `/login` normal (se renueva solo mientras se use).
- Comprobar desde TD: `os.path.exists(os.path.expanduser('~')+r'\.local\bin\claude.exe')` y `~/.claude/.credentials.json` (campo `subscriptionType`).

---

## 4. El COMP agente

Un `baseCOMP` (p. ej. `claude_agent`) con:

**DATs**
- `cc` — módulo principal (código en el anexo A).
- `callbacks` — lo único que cambia por proyecto: `onSend`, `onAnswer`, `onError`.
- `frame_exec` (Execute DAT: Start, Frame Start, Exit) — autoarranque, `poll()` cada frame, cierre al salir.
- `parexec` (Parameter Execute) — botones y cambios de parámetros.
- `log` (tabla: hora, tipo, texto; últimos 200) — mensajes, herramientas usadas, estado, errores.
- `respuesta` — última respuesta final (otros nodos pueden escucharlo con un DAT Execute).
- `instrucciones_extra` — nota fija corta que se antepone siempre (p. ej. "tu respuesta se lee en voz alta: sin markdown").
- `system_prompt_sala` — instrucciones del modo restringido (si las hay). **Guárdalas dentro del agente**, no en otro operador (en Frameroom se perdieron al borrar el Agent LOP del que se leían).

**Parámetros** (página "Claude Code")
- `Prompt` (str) + `Call` (pulse) — misma interfaz que un Agent LOP: cualquier puente que hacía `agent.par.Prompt = t; agent.par.Call.pulse()` funciona sin tocarlo.
- `Active`, `Autostart`
- `Mode` (menú): `sala` / `dev` (ver §5)
- `Model` (menú): `haiku` / `sonnet` / `opus` / `default` — los alias de Claude Code apuntan al modelo más reciente de cada familia.
- `Systemdat` (DAT, opcional): system prompt propio; vacío = el del modo.
- `Salaurl`, `Devurl` (URLs MCP), `Claudeexe` (vacío = auto)
- Pulsos: `Apply` (aplicar cambios conservando la conversación), `Newconversation`, `Start`, `Stop`
- Solo lectura: `Status`, `Currentmodel`, `Sessionid`, `Lastduration`, `Running`

**Qué necesita relanzar el proceso**
- `Mode` → relanza con **conversación nueva** (las reglas y herramientas del otro modo confunden: en ABAC, tras pasar de Sala a Dev seguía intentando usar las herramientas de Sala).
- `Model`, URLs, `Claudeexe`, `Systemdat` → relanza **conservando la conversación** (`--resume <session_id>`, menos de 1 s).
- Si está respondiendo, el cambio queda pendiente y se aplica al terminar.
- Editar el texto de las instrucciones o del `CLAUDE.md` → pulsar `Apply` o `Newconversation`.
- `callbacks`, `Active`, origen de voz → efecto inmediato.

---

## 5. Modos y herramientas

### Dev (para Framemov)
- Servidores MCP: el tool manager del proyecto **y** el td-tool completo (`td_tool` → `http://127.0.0.1:18766/mcp`).
- `--permission-mode bypassPermissions`: terminal, ficheros y todo sin preguntar (en `-p` no hay nadie que apruebe; sin esto, lo no permitido se deniega en silencio: "no pude abrir Paint porque el permiso quedó pendiente").
- Sin system prompt: el contexto lo da el `CLAUDE.md` (§6).
- Hereda los permisos de TD: si TD corre como administrador, Claude Code también. No hay forma limpia de elevar solo a Claude Code sin el aviso de Windows.

### Sala (para el público / equipo de la instalación)
- Solo el **tool manager propio** del proyecto, en `127.0.0.1` (no accesible desde la red), con herramientas concretas y validadas. **Nada de `td_code`** (Python libre).
- `--allowedTools mcp__<servidor>` y `--disallowedTools` de las herramientas propias de Claude Code: `Bash PowerShell Read Write Edit MultiEdit NotebookEdit Glob Grep LS WebFetch WebSearch Task Agent TodoWrite KillShell BashOutput`.
- System prompt con las reglas de la instalación (`system_prompt_sala`).
- Acciones con riesgo físico (mover máquinas): la herramienta **propone**, una persona **confirma** en la app. No exponer ninguna herramienta de "confirmar".
- Ojo: el td-tool del 18766 sigue escuchando en `0.0.0.0`; en el PC de la instalación conviene ponerlo en `127.0.0.1` o dejarlo solo para Tailscale.

### Tool manager propio del proyecto
Es una **copia de `/td_tool/tool_manager1`** (el tool manager de td-tool de dotsimulate) dentro de un contenedor del proyecto:

```python
c = op('/project1').create(baseCOMP, 'mi_tools')
pg = c.appendCustomPage('Config'); pg.appendToggle('Autoinicio')[0].default = True; c.par.Autoinicio = True
tm = c.copy(op('/td_tool/tool_manager1'), name='tool_manager_mi')
for n in ('restart_claude_exec', 'restart_claude_par'):      # ver gotchas
    if tm.op(n): tm.op(n).destroy()
tm.par.Servername = 'mi_tools'; tm.par.Host = '127.0.0.1'; tm.par.Port = 18767
tm.seq.Tool.numBlocks = 1
tm.par.Tool0op = '/project1/mi_tools/tools_mi'; tm.par.Tool0active = 'enabled'
# luego: Refreshtools.pulse(), Startserver.pulse()
```

- El autoarranque de la copia mira el parámetro `Autoinicio` de su contenedor padre: hay que crearlo.
- **Las Tool COMPs de LOPs (dotsimulate) son compatibles directamente**: en Frameroom, `tool_td_mod1`, `hue_agent_tool` y `web_viewer` del Agent LOP se enchufaron tal cual al tool manager propio (19 herramientas sin reescribir nada).
- Para herramientas nuevas, un COMP con una extensión que tenga `GetTool()` (anexo B).

### Formato de una herramienta (contrato del tool manager)
```python
def GetTool(self):
    return [{'tool_definition': {'type': 'function', 'function': {
                'name': 'estado_mesa', 'description': '...',
                'parameters': {'type': 'object', 'properties': {...}, 'required': [...]}}},
             'callback_info': {'handler': self.HandleEstado}}, ...]

def HandleEstado(self, tool_call):
    args = json.loads(tool_call.function.arguments or '{}')
    return {'status': 'ok', ...}          # dict serializable a JSON
```
**En `properties`, los parámetros obligatorios deben ir ANTES que los opcionales**: el tool manager genera una firma Python en ese orden y, si no, la herramienta no se registra (el error solo aparece en el log del tool manager).

Para aplicar cambios en herramientas: **Refresh Tools y después Restart Server** (en ese orden; si no, reinicia con la definición vieja), y luego `Apply` en el agente.

---

## 6. Contexto: CLAUDE.md

Claude Code sin contexto es capaz pero va a ciegas. En Frameroom, al pedirle "cárgame el Dynamic Persona", buscó en la red, encontró un Constant CHOP llamado `dynamic_persona` y no entendió nada. Claude Desktop lo entendía porque tenía los documentos del proyecto y la memoria.

Solución: un `CLAUDE.md` en la carpeta del `.toe` (Claude Code lo lee solo, porque `cwd = project.folder`). **Mínimo y solo información, sin reglas**:
- Qué es la instalación (2 líneas).
- Vocabulario propio ("proyecto" = los que carga el Loader, no el `.toe").
- **Dónde consultar lo que cambia** en vez de listas fijas (`projects.listar_proyectos()`, `/project1/projects_table`): si la lista está escrita y se añade un proyecto, Claude se pone pesado con "ese proyecto no existe".
- Cómo se llaman las herramientas propias, con un ejemplo exacto (sin esto falló dos veces el formato de `td_mod` antes de acertar; con el ejemplo, a la primera).
- Dónde está cada cosa en la red (rutas principales).

---

## 7. Entrada y salida

**Entrada**
- Patrón Agent LOP: parámetros `Prompt` + `Call` (lo más simple para sustituir un agente existente: basta con cambiar el parámetro "Agente al que enviar" de cada puente).
- Patrón STT: una CHOP Execute sobre `<origen>/stt_trigger` (0→1) que lee `<origen>/stt_text`, lo vacía y llama a `cc.send()`.
- **Nunca dos agentes escuchando el mismo trigger** (el primero que lee borra el texto). Al activar uno, desactivar el otro.

**Salida** (en `callbacks.onAnswer`)
- Por herramienta: Claude llama a una función que escribe en la app (ABAC: `voz.responder(texto)`). Como respaldo, si en ese turno no la ha llamado, mandar su texto final.
- Por texto final: TTS + app (Frameroom: `tts1.par.Inputtext = t; tts1.par.Resetplayback.pulse(); run(Synthesize.pulse, 500 ms)` y la app escucha el DAT `respuesta`).
- **Un solo camino a la app**: si la app ya escucha un DAT, no mandarlo también desde el callback (sale duplicado).
- `onError`: mostrar el fallo en la app ("⚠ No he podido arrancar Claude Code…"); si no, el mensaje se pierde sin aviso.
- **"Pensando…" en la app**: exponer en la API de la app `{pensando, detalle, segundos}` leyendo el estado del agente (`busy`, última herramienta usada → "Mirando la mesa", "Preparando la propuesta"…) y pintar una burbuja con puntos animados.
- **Selector de modelo en la app**: una ruta `POST /api/.../modelo {modelo}` que cambia `Model` (se relanza conservando la conversación). El modo Dev/Sala, solo desde TD.

---

## 7b. Voz de Claude en el dispositivo (TTS que suena en el móvil, no en el PC)

Implementado en ABAC (`/project1/ui/voz_tts`, `/project1/ui/voz/voz`) y en "Agente Claude conversacional voz" (`/claude_voz/voz_tts`, `/claude_voz/voz_app/voz`). La idea: **TD sintetiza la frase a un `.wav` y el móvil lo descarga y lo reproduce**. Los altavoces del PC quedan apagados.

```
respuesta de Claude ─► hablar(texto): limpiar → voz_tts.Inputtext → Resetplayback → (500 ms) Synthesize
voz_tts (Kokoro, Save Audio = Auto, Playback Active = Off) ─► guarda tts_kokoro_<fecha>.wav
voz_tts_callbacks.onSynthesisComplete ─► run(audio_nuevo, delayFrames=5)   (el wav se guarda DESPUÉS del callback)
audio_nuevo(): renombra a voz_<n>.wav, borra los antiguos (deja 15), avisa a la app
app ─► descarga /audio/voz_<n>.wav (o /api/voz/audio/<n>.wav) y lo reproduce con un <audio>
```

**El motor (`voz_tts`, TTS de dotsimulate con Kokoro)**
- Necesita el entorno de voz de LOPs: `uv` instalado (`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`) y `uv sync` en `%USERPROFILE%\Documents\LOPs_install\sidecars\speech_sidecar` (PyTorch CUDA, Kokoro…, unos minutos). Sin `uv` en el PATH, el botón "Install Dependencies" falla ("uv not found").
- Al copiar el componente de otro proyecto, revisar `Providersfolder` y `Saveaudiofolder` (traen rutas del otro PC) y `Callbackdat`.
- Parámetros: `Provider = kokoro`, `Voice = em_santa` (español; `ef_dora` y `em_alex` también), `Language = es-es`, `Saveaudio = auto`, `Saveaudiofolder = <proyecto>/…/audio`, `Playbackactive = False` (no suena en el PC).
- **No usar "Initialize On Start"** (inicializa mientras la extensión aún se construye y la rompe: `AttributeError AssignedCallbacks`, luego "No provider selected") **ni "Auto Reattach"** (se engancha a un proceso viejo y se queda en Shutdown).
- Arranque propio: un Execute DAT `voz_tts_arranque` (Start + Exit). En `onStart`: vaciar `Inputtext` (si no, al arrancar dice la última frase guardada), matar procesos `lops_worker_*` huérfanos y, 6 s después, fijar voz e idioma y pulsar `Initialize`, reintentando cada 5 s hasta `Enginestatus == 'Ready'`. En `onExit`: `Shutdown` y matar los procesos. Tarda 20-30 s en estar listo tras abrir el `.toe`.
- **El componente vuelve a inglés (`af_heart`, `en-us`) cada vez que se reinicializa por su cuenta** (p. ej. al cambiar `Playbackactive`), y Kokoro fija el idioma al inicializar: poner "español" después no basta, lee el texto español con fonética inglesa. En `voz_tts_callbacks.onInitialize`: si voz/idioma no son los buenos, corregirlos y hacer un **reinicio limpio** (Shutdown → matar `lops_worker_*` → Initialize). Reinicializar encima de un motor en marcha deja procesos cruzados (`Worker error: unauthorized`).
- **Pulsar `Resetplayback` antes de cada frase y sintetizar 500 ms después**: si no, a veces se queda muda.
- El seseo ("c"/"z" como "s") es de la voz, no del texto: Kokoro genera bien la θ en los fonemas, pero las voces la pronuncian como s. Solo se arregla cambiando de proveedor (ElevenLabs).
- Avisos inofensivos en el log: `Defaulting repo_id to hexgrad/Kokoro-82M`, `HF_TOKEN`, symlinks de Windows.

**Texto antes de sintetizar (`_limpiar`)**: quitar enlaces, markdown y emojis; unidades detrás de un número a palabras ("6 s" → "6 segundos", "32 cm" → "32 centímetros", cm/s, mm, °, %, ms, min, fps; singular si es 1); y una tabla `PRONUNCIA` solo para lo que se oye ("mover" → "múver", "XPlanar" → "equis plánar", "TwinCAT" → "tuin cat", "Claude" → "clod", "app" → "ap", "OK" → "oquéi"…; sustituir solo palabras sueltas y respetar la mayúscula inicial). En la app el texto se ve sin cambios.

**Avisar a la app**
- Con WebSocket (agente conversacional): `broadcast(c, {'type': 'audio', 'url': '/audio/voz_<n>.wav?t=<ms>'})`. Mandar a la vez el `answer` con `speak: False` para que no hable también la voz del navegador. Si Kokoro no está `Ready`, mandar `speak: True` y usar la voz del navegador como respaldo.
- Sin WebSocket (ABAC): contador en `storage` y ruta `GET /api/voz/audio` → `{n, dispositivo}`; la página pregunta cada 600 ms y reproduce cuando **`n` es distinto** del último (no mayor: al reabrir TD el contador guardado en el `.toe` vuelve atrás y la app se quedaba muda).
- Servir el fichero desde el Web Server DAT con `content-type: audio/wav` y `cache-control: no-store`. Validar el nombre (`voz_\d+\.wav`).
- **Si el DAT de callbacks del servidor se carga desde un fichero (`file` + `loadonstart`), editar también el fichero**: en ABAC la ruta del audio desapareció al reiniciar TD porque solo estaba en el DAT.

**En la página**
```js
const AU=new Audio(); AU.preload='auto'; AU.setAttribute('playsinline','');
// iOS/Android solo dejan sonar audio tras un toque: el primero lo "desbloquea" con un wav silencioso
let ok=false; const SIL='data:audio/wav;base64,...';
const unlock=()=>{ if(ok) return; ok=true; AU.src=SIL; AU.play().catch(()=>{ok=false}); };
document.addEventListener('touchend',unlock,{passive:true}); document.addEventListener('click',unlock,true);
function playAudio(url){ AU.pause(); AU.src=url; AU.play().catch(()=>{}); }   // pausar el micro mientras suena
```
- Cada vez que se recarga la página hace falta un toque en la pantalla antes de que suene.
- Todas las páginas abiertas reproducen la voz (también el navegador del PC si tiene la app abierta).
- Tras una nota de voz el micrófono debe estar pausado mientras suena la respuesta (si no, Claude se oye a sí mismo).

**Interruptor en TD**: parámetro "Dónde suena la voz" (PC / dispositivo / los dos) que pone `Playbackactive` del TTS y hace el reinicio limpio (cambiarlo reinicializa el motor).

### Acceso desde la red local y por Tailscale a la vez
- Tailscale: `tailscale serve --bg --https=8443 http://127.0.0.1:<puerto>` → `https://<pc>.<tailnet>.ts.net:8443`. Si la dirección `.ts.net` no carga, comprobar `tailscale serve status` ("No serve config" = falta ese comando en ese PC).
- Red local (iPad sin Tailscale): **segundo Web Server DAT** (`web_lan`, p. ej. puerto 9991) con `secure` y certificado propio, y los **mismos callbacks**. No hace falta mandar nada al iPad: la primera vez Safari avisa ("Mostrar detalles" → "visitar este sitio web") y ya no vuelve a preguntar. Instalar el certificado como de confianza en el dispositivo solo merece la pena si lo va a usar mucha gente.
- **Con WebSocket y dos servidores, contestar a cada dispositivo por el servidor por el que entró**: en `onWebSocketOpen` guardar `client → webServerDAT.path` y enviar con ese DAT (`_dat(c, client).webSocketSendText(...)`). Así el iPad por red local y el iPhone por Tailscale reciben respuestas y audio a la vez, sin elegir "servidor activo".
- Cortafuegos: la regla de entrada de Windows es por versión de `TouchDesigner.exe`; si se actualiza TD, comprobar que la versión nueva tiene la suya.

---

## 8. Gotchas ya resueltos

1. **TD no ve `~/.local/bin`** → ruta completa a `claude.exe`.
2. **No bloquear el hilo principal de TD** (subprocess síncronos que esperan a Claude, `netstat` lentos…): Claude llama herramientas que se ejecutan en ese hilo → bloqueo. Todo asíncrono; para pruebas, `Popen` + leer el resultado unos frames después.
3. **TD en pausa = herramientas colgadas**: si la timeline está pausada, las llamadas por td-tool con frames de espera no vuelven nunca.
4. **El botón "Restart Claude" del td-tool hace `taskkill /IM Claude.exe`**, que mata también a Claude Code (mismo nombre). Parche: matar solo los `claude.exe` cuya ruta no esté en `\.local\bin\`. Y quitar `restart_claude_exec` de las copias del tool manager (si no, dos reinicios).
5. **Parameter Execute creado antes que los parámetros** no se engancha a ellos: volver a asignar `par.op` (`''` y luego la ruta) o crear los parámetros antes.
6. **`Running` y similares se guardan en el `.toe`**: ponerlos a False en `onStart`.
7. **Cambiar de modo con `--resume`** mezcla contextos → cambio de modo = conversación nueva.
8. **Prompts en otro operador**: si se borra ese operador, el agente se queda sin instrucciones. Guardarlos dentro del agente.
9. **Micrófono en el móvil (Web Speech API) exige https**: Safari en iPad/iPhone por http a veces sí y a veces no (por IP de Tailscale da `not-allowed`); Chrome en Android, no. Soluciones:
   - Tailscale: `tailscale serve --bg --https=8443 http://127.0.0.1:<puerto>` → `https://<pc>.<tailnet>.ts.net:8443` (certificado real, persiste).
   - Red local: segundo Web Server DAT con `secure` y certificado propio (generado con `cryptography`: SAN con las IPs del PC, `serverAuth`, validez ≤ 825 días para iOS). El público ve "conexión no privada" una vez → tutorial en pantalla (ABAC: tecla 1).
   - En la página: `new WebSocket((location.protocol==="https:"?"wss://":"ws://")+location.host)`.
10. **Dictado largo cortado**: `SpeechRecognition` con `continuous=false` corta en la primera pausa. Usar `continuous=true`, reengancharse en `onend` acumulando el texto, y enviar al tocar de nuevo (tope de seguridad de unos minutos).
11. **Conversaciones duplicadas al reanudar**: deduplicar por el uuid del primer mensaje (en las pruebas, `--resume` mantuvo el id, pero conviene ser robusto).
12. **Windows y dos servidores en el mismo puerto** (`SO_REUSEADDR`): en servidores Python propios, `allow_reuse_address=False` + `SO_EXCLUSIVEADDRUSE`.

**Latencias medidas** (con la conversación ya caliente): 2–4 s para una orden con una llamada a herramienta (Haiku/Sonnet). La primera respuesta tras arrancar, 6–9 s. Coreografías o tareas largas con Opus: más.

---

## 9. Checklist para una instalación nueva

1. Requisitos del PC (§3).
2. Mirar el proyecto con td-tool: de dónde salen las órdenes (app, STT, Agent LOP existente), adónde deben ir las respuestas (TTS, app, DAT) y qué herramientas hacen falta.
3. Tool manager propio en `127.0.0.1:18767` con las herramientas de la instalación (reusar Tool COMPs de LOPs si existen).
4. COMP agente (anexo A) + `callbacks` adaptados.
5. `CLAUDE.md` mínimo en la carpeta del `.toe`.
6. Apuntar las entradas al agente (o el parámetro "Agente al que enviar"), desactivando el sistema anterior. **No borrar el sistema anterior hasta probar**; dejar claro cómo volver.
7. Probar: arranque en frío (cerrar y abrir el `.toe`), una orden simple, una con herramienta, cambio de modelo, error (Claude Code no instalado → aviso en la app).
8. Guardar el `.toe` (versión nueva).

---

## Anexo A — módulo `cc` (versión Frameroom)

```python
"""
cc -- Claude Code (CLI) como agente persistente dentro de TD.
Mismos parametros Prompt + Call que un Agent LOP.
Modos: sala (solo el tool manager del proyecto) / dev (+ td-tool completo, todo permitido).
"""
import subprocess, threading, queue, json, os, sys, time, shutil

BLOCK = ['Bash', 'PowerShell', 'Read', 'Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'Glob', 'Grep', 'LS',
         'WebFetch', 'WebSearch', 'Task', 'Agent', 'TodoWrite', 'KillShell', 'BashOutput']
SERVER = 'frameroom_tools'          # nombre del tool manager del proyecto


def _reg():
    if not hasattr(sys, '_cc_agents'):
        sys._cc_agents = {}
    return sys._cc_agents


def _st(c):
    return _reg().setdefault(c.path, {'proc': None, 'q': None, 'busy': False, 't0': 0, 'session': '',
                                      'pending': False, 'errpath': ''})


def _log(c, kind, text):
    t = c.op('log')
    t.appendRow([time.strftime('%H:%M:%S'), kind, str(text).replace('\t', ' ').replace('\n', ' ')[:500]])
    while t.numRows > 201:
        t.deleteRow(1)


def _status(c, s):
    c.par.Status = s
    _log(c, 'status', s)


def _call(c, name, *a):
    d = c.op('callbacks')
    try:
        f = getattr(d.module, name, None)
        if f:
            f(c, *a)
    except Exception as e:
        _log(c, 'error', '%s: %s' % (name, e))


def exe(c):
    p = c.par.Claudeexe.eval().strip()
    if p and os.path.exists(p):
        return p
    p = os.path.join(os.path.expanduser('~'), '.local', 'bin', 'claude.exe')
    return p if os.path.exists(p) else (shutil.which('claude') or '')


def running(c):
    p = _st(c)['proc']
    return p is not None and p.poll() is None


def start(c, resume=''):
    if running(c):
        return True
    x = exe(c)
    if not x:
        _status(c, 'ERROR: no encuentro claude.exe')
        return False
    mode = c.par.Mode.eval()
    servers = {SERVER: {'type': 'http', 'url': c.par.Salaurl.eval()}}
    allowed = ['mcp__' + SERVER]
    if mode == 'dev':
        servers['td_tool'] = {'type': 'http', 'url': c.par.Devurl.eval()}
        allowed += ['mcp__td_tool']
    args = [x, '-p', '--input-format', 'stream-json', '--output-format', 'stream-json', '--verbose',
            '--strict-mcp-config', '--mcp-config', json.dumps({'mcpServers': servers}),
            '--allowedTools'] + allowed
    if mode == 'dev':
        args += ['--permission-mode', 'bypassPermissions']
    else:
        args += ['--disallowedTools'] + BLOCK
    if c.par.Model.eval() != 'default':
        args += ['--model', c.par.Model.eval()]
    # instrucciones: nota fija + (Systemdat, o el del modo: sala -> system_prompt_sala; dev -> nada, manda CLAUDE.md)
    base = c.par.Systemdat.eval() or (c.op('system_prompt_sala') if mode == 'sala' else None)
    sp = (c.op('instrucciones_extra').text + '\n' + (base.text if base else '')).strip()
    if sp:
        args += ['--append-system-prompt', sp]
    if resume:
        args += ['--resume', resume]
    st = _st(c)
    st['errpath'] = os.path.join(os.environ.get('TEMP', project.folder), 'claude_agent_stderr.txt')
    env = dict(os.environ); env.setdefault('ENABLE_TOOL_SEARCH', 'false')
    try:
        proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=open(st['errpath'], 'w', encoding='utf-8'), cwd=project.folder, env=env,
                                text=True, encoding='utf-8', errors='replace', bufsize=1,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    except Exception as e:
        _status(c, 'ERROR al arrancar: %s' % e)
        return False
    q = queue.Queue()

    def reader(s, qq):
        try:
            for line in s:
                qq.put(line)
        except Exception:
            pass
        qq.put(None)
    threading.Thread(target=reader, args=(proc.stdout, q), daemon=True).start()
    st.update(proc=proc, q=q, busy=False, session=resume or '', pending=False)
    c.par.Running = True
    c.par.Sessionid = resume or ''
    _status(c, 'Arrancado: modo %s, modelo %s%s' % (mode, c.par.Model.eval(), ', conversacion recuperada' if resume else ''))
    return True


def stop(c, msg='Parado'):
    st = _st(c); p = st['proc']
    if p is not None and p.poll() is None:
        try:
            p.stdin.close()
        except Exception:
            pass
        try:   # /T: tambien los hijos de claude.exe
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(p.pid)], capture_output=True, timeout=5,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:
            pass
    st.update(proc=None, q=None, busy=False)
    c.par.Running = False
    _status(c, msg)


def restart(c):
    stop(c, 'Conversacion nueva')
    return start(c)


def apply(c, nueva=False):
    st = _st(c)
    if not running(c):
        _status(c, 'Cambios guardados (se aplican al arrancar)')
        return
    if st['busy']:
        st['pending'] = 'nueva' if (nueva or st['pending'] == 'nueva') else True
        _status(c, 'Cambios pendientes: se aplican al terminar esta respuesta')
        return
    sid = '' if nueva else (st['session'] or c.par.Sessionid.eval())
    stop(c, 'Aplicando cambios...')
    start(c, resume=sid)


def send(c, text):
    text = (text or '').strip()
    if not text:
        return False
    if not c.par.Active.eval():
        _status(c, 'Inactivo: mensaje ignorado')
        return False
    if not running(c) and not start(c):
        _call(c, 'onError', 'No he podido arrancar Claude Code: ' + c.par.Status.eval())
        return False
    st = _st(c)
    msg = {'type': 'user', 'message': {'role': 'user', 'content': [{'type': 'text', 'text': text}]}}
    try:
        st['proc'].stdin.write(json.dumps(msg, ensure_ascii=False) + '\n'); st['proc'].stdin.flush()
    except Exception as e:
        stop(c, 'ERROR enviando (%s)' % e)
        _call(c, 'onError', 'Claude Code se ha caido; repite la orden.')
        return False
    st['busy'] = True; st['t0'] = time.time()
    _log(c, 'user', text)
    _status(c, 'Pensando...')
    _call(c, 'onSend', text)
    return True


def poll(c):
    st = _st(c); q = st.get('q')
    if q is None:
        return
    for _ in range(200):
        try:
            line = q.get_nowait()
        except queue.Empty:
            return
        if line is None:                      # el proceso ha terminado
            era = st['busy']; code = st['proc'].poll() if st['proc'] else None
            err = ''
            try:
                err = open(st['errpath'], encoding='utf-8', errors='replace').read()[-300:].strip()
            except Exception:
                pass
            stop(c, 'Proceso terminado (codigo %s). %s' % (code, err))
            if era:
                _call(c, 'onError', 'Claude Code se ha cerrado mientras respondia.')
            return
        try:
            ev = json.loads(line)
        except Exception:
            continue
        _handle(c, st, ev)


def _handle(c, st, ev):
    t = ev.get('type')
    if t == 'system' and ev.get('subtype') == 'init':
        st['session'] = ev.get('session_id', ''); c.par.Sessionid = st['session']
        c.par.Currentmodel = ev.get('model', '')
        mcps = ', '.join('%s:%s' % (m.get('name'), m.get('status')) for m in ev.get('mcp_servers', []))
        _log(c, 'init', 'sesion %s | %s' % (st['session'][:8], mcps))
    elif t == 'assistant':
        for b in (ev.get('message') or {}).get('content') or []:
            if b.get('type') == 'tool_use':
                n = b.get('name', '')
                _log(c, 'tool', n + ' ' + json.dumps(b.get('input', {}), ensure_ascii=False)[:300])
                c.par.Status = 'Usando %s...' % n.split('__')[-1]
            elif b.get('type') == 'text' and b.get('text', '').strip():
                _log(c, 'text', b['text'])
    elif t == 'result':
        st['busy'] = False
        dur = ev.get('duration_ms', 0) / 1000.0
        c.par.Lastduration = round(dur, 2)
        text = (ev.get('result') or '').strip()
        if ev.get('is_error'):
            _status(c, 'ERROR: %s' % (text or ev.get('subtype')))
            _call(c, 'onError', text or 'error')
        else:
            _status(c, 'Listo (%.1f s)' % dur)
            c.op('respuesta').text = text
            _call(c, 'onAnswer', text, ev)
        if st['pending']:
            nueva = st['pending'] == 'nueva'; st['pending'] = False
            run("mod(args[0]).apply(op(args[1]), nueva=args[2])", c.op('cc').path, c.path, nueva, delayFrames=2)
```

**`frame_exec`** (Execute DAT con Start, Frame Start y Exit activados):
```python
def onStart():
    b = me.parent()
    b.par.Running = False
    if b.par.Active.eval() and b.par.Autostart.eval():
        # margen para que los tool managers esten levantados
        run("mod(args[0]).start(op(args[1]))", b.op('cc').path, b.path, delayFrames=240)

def onFrameStart(frame):
    mod(me.parent().op('cc')).poll(me.parent())

def onExit():
    mod(me.parent().op('cc')).stop(me.parent(), 'TD cerrado')
```

**`parexec`** (Parameter Execute: `op` = el agente; `pars` = lista explícita; On Pulse y Value Change):
```python
RESTART = ('Model', 'Salaurl', 'Devurl', 'Claudeexe', 'Systemdat')

def onPulse(par):
    b = par.owner; m = mod(b.op('cc'))
    if par.name == 'Call':            m.send(b, b.par.Prompt.eval())
    elif par.name == 'Start':         m.start(b)
    elif par.name == 'Stop':          m.stop(b)
    elif par.name == 'Newconversation': m.restart(b)
    elif par.name == 'Apply':         m.apply(b)

def onValueChange(par, prev):
    b = par.owner; m = mod(b.op('cc'))
    if par.name == 'Mode':            m.apply(b, nueva=True)
    elif par.name in RESTART:         m.apply(b)
    elif par.name == 'Active':
        if par.eval():
            if b.par.Autostart.eval(): m.start(b)
        else:
            m.stop(b, 'Desactivado')
```

**`callbacks`** (ejemplo Frameroom):
```python
def _decir(texto):
    tts = op('/project1/LOPS/tts1')
    if tts is not None:
        tts.par.Inputtext = texto
        tts.par.Resetplayback.pulse()
        run("args[0].par.Synthesize.pulse()", tts, delayMilliSeconds=500)

def onSend(agent, text):
    return

def onAnswer(agent, text, info):
    if text:
        _decir(text)          # el texto al iPad lo manda app_bridge al cambiar el DAT 'respuesta'

def onError(agent, msg):
    cb = op('/project1/app_bridge/app_webserver_callbacks')
    if cb is not None:
        mod(cb).difundir_respuesta('⚠ ' + msg)
```

---

## Anexo B — Tool COMP propio (esqueleto)

`baseCOMP` con un Text DAT `MiToolsEXT` y `ext0object = op('./MiToolsEXT').module.MiToolsEXT(me)`, `ext0promote = True`, luego `initializeExtensions()`.

```python
import json

def _fn(name, desc, props=None, required=None):
    return {'type': 'function', 'function': {'name': name, 'description': desc,
            'parameters': {'type': 'object', 'properties': props or {}, 'required': required or []}}}

class MiToolsEXT:
    def __init__(self, ownerComp):
        self.ownerComp = ownerComp

    def GetTool(self):
        return [
            {'tool_definition': _fn('estado', 'Estado de la instalacion. Llamalo antes de actuar.'),
             'callback_info': {'handler': self.HandleEstado}},
            {'tool_definition': _fn('proponer', 'Propone un cambio; NO lo ejecuta (se confirma en la app).',
                 {'destino': {'type': 'string'},            # obligatorios primero
                  'nombre': {'type': 'string'},
                  'explicacion': {'type': 'string'}},       # opcionales despues
                 ['destino', 'nombre']),
             'callback_info': {'handler': self.HandleProponer}},
        ]

    def HandleEstado(self, tool_call):
        return {'status': 'ok', 'modo': '...'}

    def HandleProponer(self, tool_call):
        a = json.loads(tool_call.function.arguments or '{}')
        try:
            # ... validar y guardar la propuesta ...
            return {'status': 'ok', 'nota': 'La app la ensena; la persona confirma alli.'}
        except Exception as e:
            return {'status': 'error', 'error': str(e)}
```

---

## Anexo C — Claude TD (desarrollar sin Claude Desktop)

App local (`~/Claude/claude-td`, Python sin dependencias + Edge en modo app) para trabajar con Claude Code en los proyectos de `Documents\TouchDesigner`:
- Proyecto = carpeta; `CLAUDE.md` = instrucciones del proyecto (el antiguo master.md); `docs/` = documentos.
- Conversaciones guardadas por Claude Code en `~/.claude/projects/<carpeta>/<id>.jsonl`; la app las lista, las retoma (`--resume`) y las borra (a `papelera/`). No viajan entre PCs; lo que está en la carpeta del proyecto, sí.
- Texto en directo (`--include-partial-messages`), llamadas a td-tool plegadas, selector de modelo y de permisos, reconexión automática a td-tool si TD se abre después.
- Portable: copiar la carpeta; requiere Python 3 en el PATH y Claude Code con login. Al actualizar ficheros, cerrar el servidor viejo: `Get-Process pythonw | Stop-Process`.

---

## Cómo pedírselo a un chat nuevo

> Te adjunto la guía "Agentes de Claude Code dentro de TouchDesigner". Tienes el td-tool del PC de la instalación por MCP. Mira el proyecto abierto, dime de dónde salen las órdenes, adónde deben ir las respuestas y qué herramientas propondrías para el modo Sala, y monta el agente siguiendo la guía. No borres el sistema actual hasta que lo hayamos probado.
