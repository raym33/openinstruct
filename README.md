# OpenInstruct

CLI local para usar agentes LLM open source en modo parecido a `claude code` o `codex`, pero apuntando a modelos servidos por `Ollama` o `LM Studio`.

Ahora también incluye una base de IDE local sobre `Code - OSS` / `VS Code`:

- daemon HTTP local `openinstructd`
- extensión ligera para `Code - OSS` / `VS Code`
- launcher portable para macOS orientado a `Apple Silicon`

## Qué incluye

- REPL interactivo para trabajar sobre un proyecto local.
- Modo `run` para tareas one-shot.
- Adaptador nativo para `Ollama`.
- Adaptador OpenAI-compatible para `LM Studio`.
- Agente con herramientas locales:
  - listar directorios
  - expandir globs
  - leer ficheros
  - buscar texto con `rg`
  - inspeccionar `git status` y `git diff`
  - escribir y editar ficheros
  - crear directorios
  - memoria por proyecto en `.openinstruct/project.md`
  - ejecutar comandos shell con política de aprobación
- Orquestación de subagentes en paralelo para repartir subtareas.
- Planificador maestro que divide objetivos grandes en un grafo de tareas con dependencias.
- Locks por fichero y por workspace para evitar colisiones entre subagentes que escriben a la vez.
- Checkpoints persistidos y reintentos por tarea fallida dentro del DAG.
- Tareas en background para lanzar agentes aislados y seguir usando el CLI.
- Sesiones persistentes en `~/.openinstruct/sessions`.
- Backends de memoria a largo plazo con `mem0` o `sqlite`.
- Daemon local `openinstructd` para integraciones IDE o TUI.
- UI web móvil servida desde el propio daemon.
- Extensión `Code - OSS` / `VS Code` con panel de chat y árbol de sesiones.
- Launcher portable para montar un mini IDE local en macOS.
- Modo `knowledge base` para compilar `raw/` en una wiki markdown y derivar respuestas, slides y figuras.
- Slash commands para cambiar modelo, proveedor, carpeta de trabajo, política de aprobación, inicializar memoria y revisar diffs.
- Comando `doctor` para comprobar que tu runtime local está disponible y qué modelos expone.

## Instalación

Desde el directorio del proyecto:

```bash
cd /Users/c/Desktop/carpeta\ sin\ título\ 2/openinstruct
python3 -m pip install -e .
```

Si quieres activar memoria a largo plazo con `mem0`:

```bash
python3 -m pip install -e .[mem0]
```

Luego podrás usar:

```bash
openinstruct chat
openinstruct run "analiza este repo y propón mejoras"
openinstruct doctor
openinstruct daemon --port 8765
openinstruct mobile publish --provider ollama --model qwen2.5-coder:14b
openinstruct kb init
```

También puedes ejecutarlo sin instalar:

```bash
cd /Users/c/Desktop/carpeta\ sin\ título\ 2/openinstruct
python3 -m openinstruct chat
```

Y si quieres arrancar solo el daemon:

```bash
python3 -m openinstruct daemon --host 127.0.0.1 --port 8765
```

## Requisitos

- Python `3.9+`
- Uno de estos proveedores levantado en local:
  - `Ollama` en `http://127.0.0.1:11434`
  - `LM Studio` en `http://127.0.0.1:1234/v1`

Para `mem0`:

- `mem0ai` instalado de forma opcional
- Python `3.10+` para ese backend concreto

## Uso rápido

### 1. Verifica el proveedor

```bash
openinstruct doctor
```

### 2. Arranca un chat

```bash
openinstruct chat --provider ollama --model qwen2.5-coder:7b
```

o

```bash
openinstruct chat --provider lmstudio --model local-model
```

### 2b. O arranca el daemon para IDE

```bash
openinstruct daemon --provider ollama --model qwen2.5-coder:14b --port 8765
```

### 3. Pide una tarea de agente

Ejemplos:

```text
revisa este proyecto y crea un README mejor
```

```text
encuentra el bug en la validación y arréglalo
```

```text
crea una API FastAPI mínima con tests
```

## Knowledge Base Mode

OpenInstruct también puede usarse como compilador local de conocimiento, orientado a un flujo tipo Obsidian:

- `raw/`: material fuente bruto
- `wiki/`: wiki compilada y enlazada
- `outputs/`: respuestas, slides, figuras y otros derivados

Inicializa la estructura:

```bash
openinstruct kb init
```

Estado del workspace:

```bash
openinstruct kb status
```

Actualizar el manifiesto incremental de fuentes:

```bash
openinstruct kb ingest
```

Compilar `raw/` hacia la wiki:

```bash
openinstruct kb compile --provider ollama --model qwen2.5-coder:14b
```

Hacer una pregunta y archivarla como markdown:

```bash
openinstruct kb ask --provider ollama --model qwen2.5-coder:14b "compara los enfoques de memoria entre mem0 y un wiki autocompilado"
```

Generar un deck Marp:

```bash
openinstruct kb ask --format marp --provider ollama --model qwen2.5-coder:14b "resume el estado del arte en agentes locales"
```

Auditar la wiki:

```bash
openinstruct kb lint --provider ollama --model qwen2.5-coder:14b
openinstruct kb lint --fix --approval-policy auto --provider ollama --model qwen2.5-coder:14b
```

Layout generado:

- `raw/README.md`
- `wiki/index.md`
- `wiki/sources/`
- `wiki/concepts/`
- `wiki/queries/`
- `outputs/slides/`
- `outputs/figures/`
- `.openinstruct/kb.json`
- `.openinstruct/sources.json`

Flujo incremental recomendado:

1. Añade o actualiza documentos en `raw/`.
2. Ejecuta `openinstruct kb ingest` para recalcular hashes y detectar `added/modified/removed`.
3. Ejecuta `openinstruct kb compile` para que el agente priorice solo las fuentes nuevas o cambiadas.

## Slash Commands

Dentro del REPL:

- `/help`
- `/status`
- `/models`
- `/provider ollama`
- `/model qwen2.5-coder:7b`
- `/pwd`
- `/cd ./otro-directorio`
- `/approval ask`
- `/agents 4`
- `/retries 1`
- `/parallel revisa auth || revisa tests || revisa docs`
- `/plan arregla el flujo de login y añade tests`
- `/delegate arregla el flujo de login y añade tests`
- `/session-spawn revisa auth y prepara hallazgos`
- `/session-send sess_... continúa con tests`
- `/session-visibility <self|tree|all>`
- `/session-status sess_...`
- `/session-history sess_...`
- `/background revisa la arquitectura y prepara un resumen`
- `/backgrounds`
- `/waitbg bg_...`
- `/locks`
- `/merge`
- `/checkpoints`
- `/resume-checkpoint <run_id>`
- `/history 8`
- `/compact 8`
- `/init`
- `/memory`
- `/memories [query]`
- `/memory-policy <none|selective|all>`
- `/kb-init [name]`
- `/kb-status`
- `/kb-ingest`
- `/kb-compile [scope]`
- `/kb-ask <question>`
- `/kb-slide <question>`
- `/kb-lint [fix]`
- `/diff`
- `/review`
- `/sessions`
- `/saved-sessions`
- `/save mi-sesion`
- `/load mi-sesion`
- `/reset`
- `/run ls -la`
- `/exit`

## Políticas de aprobación

- `ask`: pide confirmación antes de acciones con cambios o comandos shell.
- `auto`: ejecuta directamente herramientas mutables y comandos.
- `deny`: bloquea escrituras y comandos shell.

Ejemplo:

```bash
openinstruct chat --approval-policy ask
```

Para activar memoria a largo plazo:

```bash
openinstruct chat --memory-backend sqlite
```

## Variables de entorno

- `OPENINSTRUCT_PROVIDER`
- `OPENINSTRUCT_MODEL`
- `OPENINSTRUCT_OLLAMA_URL`
- `OPENINSTRUCT_LMSTUDIO_URL`
- `OPENINSTRUCT_WORKDIR`
- `OPENINSTRUCT_APPROVAL_POLICY`
- `OPENINSTRUCT_MAX_STEPS`
- `OPENINSTRUCT_MAX_AGENTS`
- `OPENINSTRUCT_TASK_RETRIES`
- `OPENINSTRUCT_MEMORY_BACKEND`
- `OPENINSTRUCT_MEMORY_POLICY`
- `OPENINSTRUCT_MEMORY_SEARCH_LIMIT`
- `OPENINSTRUCT_TEMPERATURE`
- `OPENINSTRUCT_HOME`
- `OPENINSTRUCT_SQLITE_MEMORY_PATH`
- `OPENINSTRUCT_MEM0_USER_ID`
- `OPENINSTRUCT_MEM0_AGENT_ID`
- `OPENINSTRUCT_MEM0_LLM_MODEL`
- `OPENINSTRUCT_MEM0_EMBED_MODEL`
- `OPENINSTRUCT_MEM0_SEARCH_LIMIT`

## Daemon HTTP

`openinstructd` expone una API local para el panel del editor y futuras TUIs:

```bash
openinstructd --provider ollama --model qwen2.5-coder:14b --workdir /ruta/al/proyecto
```

Endpoints principales:

- `GET /`
- `GET /health`
- `GET /api/state`
- `GET /api/jobs`
- `POST /api/jobs`
- `GET /api/jobs/<job_id>`
- `GET /api/sessions`
- `GET /api/sessions/<session_id>/status`
- `GET /api/sessions/<session_id>`
- `POST /api/sessions`
- `POST /api/sessions/<session_id>/messages`

`GET /` sirve una UI web móvil responsive para lanzar prompts, slash commands y seguir jobs/sesiones desde Safari.

`POST /api/jobs` acepta:

```json
{
  "kind": "prompt",
  "input": "revisa el repo y resume riesgos"
}
```

o:

```json
{
  "kind": "command",
  "input": "/delegate arregla login y añade tests"
}
```

`POST /api/sessions` también acepta:

```json
{
  "prompt": "revisa auth",
  "write": false,
  "visibility": "tree"
}
```

Scopes de visibilidad por sesión:

- `self`: la sesión solo puede verse a sí misma.
- `tree`: la sesión puede ver su propia rama de descendientes.
- `all`: la sesión puede ver y controlar cualquier sesión gestionada.

El runtime principal usa `all` por defecto. Las sesiones nuevas nacen con `tree` salvo que indiques otro valor.

## Mobile / Tailscale

Para usar el daemon desde un iPhone:

```bash
openinstruct mobile publish \
  --provider ollama \
  --model qwen2.5-coder:14b \
  --workdir /ruta/al/proyecto
```

Ese comando:

1. arranca `openinstructd` en `127.0.0.1:<port>` si todavía no está levantado
2. publica la UI móvil con `tailscale serve`
3. guarda metadata y logs en `~/.openinstruct/mobile-ui/`

Flags útiles:

- `--https-port 443`
- `--path /mobile`
- `--no-start-daemon`
- `--reset`
- `--tailscale-command tailscale`
- `--daemon-command openinstructd`

Notas:

- necesita `tailscale` instalado y autenticado en el Mac
- el proxy de `tailscale serve` apunta a `http://127.0.0.1:<port>`
- aquí no pude probar una publicación real porque `tailscale` no estaba instalado en el sandbox

## Extensión Code - OSS / VS Code

La extensión está en [apps/vscode-extension](/Users/c/Desktop/carpeta%20sin%20ti%CC%81tulo%202/openinstruct/apps/vscode-extension). No necesita build step.

Flujo de desarrollo:

```bash
cd /Users/c/Desktop/carpeta\ sin\ título\ 2/openinstruct/apps/vscode-extension
```

Luego abre esa carpeta con `Code - OSS` o `VS Code` y lanza la Extension Host con `F5`.

Qué añade:

- comando `OpenInstruct: Start Daemon`
- panel `OpenInstruct Chat`
- árbol `Sessions` en la activity bar
- autostart opcional del daemon contra el workspace activo

Settings útiles:

- `openinstruct.server.command`
- `openinstruct.server.args`
- `openinstruct.provider`
- `openinstruct.model`
- `openinstruct.approvalPolicy`
- `openinstruct.memoryBackend`
- `openinstruct.memoryPolicy`

## Launcher macOS

Para montar un mini IDE local en macOS con perfil portable:

```bash
cd /Users/c/Desktop/carpeta\ sin\ título\ 2/openinstruct
python3 scripts/launch_mini_vscode.py \
  --workspace /ruta/a/tu/proyecto \
  --provider ollama \
  --model qwen2.5-coder:14b \
  --approval-policy auto
```

El launcher:

- detecta `Code - OSS`, `Visual Studio Code` o `VSCodium` en `/Applications`
- crea un perfil portable en `~/.openinstruct/studio/default`
- injerta la extensión local en `extensions/`
- escribe `settings.json` con la configuración de `openinstructd`
- abre el editor apuntando al workspace seleccionado

Si no tienes `openinstructd` en el `PATH`, puedes fijar otro binario:

```bash
python3 scripts/launch_mini_vscode.py \
  --daemon-command python3 \
  --daemon-args=-m \
  --daemon-args=openinstruct.daemon
```

## Configuración opcional

Puedes crear un fichero base:

```bash
openinstruct config init
```

Se guarda en `~/.openinstruct/config.json`.

## Memoria de proyecto

Dentro del REPL:

```text
/init
```

Eso crea `.openinstruct/project.md` en tu workspace con un resumen base del proyecto. OpenInstruct lo vuelve a inyectar automáticamente en nuevas sesiones y tras `/reset`.

## Memoria a Largo Plazo

`--memory-backend mem0` o `--memory-backend sqlite` añaden memoria persistente entre sesiones.

Comportamiento actual:

- recupera recuerdos relevantes antes de cada tarea
- permite inspeccionar recuerdos desde `/memories`
- por defecto usa `--memory-policy selective` para guardar solo hechos útiles
- usa `user_id`, `agent_id` y `run_id` para separar contexto
- `mem0` puede apuntar a `Ollama` o `LM Studio` usando la misma configuración base del CLI
- `sqlite` funciona en local sin dependencias extra y guarda en `~/.openinstruct/memory.sqlite3`

Ejemplos:

```bash
openinstruct chat --provider ollama --model qwen2.5-coder:14b --memory-backend mem0 --memory-policy selective
```

```bash
OPENINSTRUCT_MEMORY_BACKEND=mem0 OPENINSTRUCT_MEM0_EMBED_MODEL=nomic-embed-text openinstruct chat
```

```bash
openinstruct chat --provider ollama --model qwen2.5-coder:14b --memory-backend sqlite --memory-policy selective
```

Dentro del REPL:

```text
/memories
/memories obsidian wiki backlinks
/memory-policy all
```

## Subagentes en paralelo

Dentro del REPL puedes repartir trabajo entre varios agentes:

```text
/agents 4
/parallel revisa el módulo auth || busca tests rotos || resume la arquitectura
```

Notas prácticas:

- `max_agents` controla el grado máximo de paralelismo.
- `task_retries` controla cuántos reintentos hace OpenInstruct antes de dar una tarea por fallida.
- El agente principal también puede usar la tool interna `spawn_agents(...)` cuando detecta subtareas independientes.
- Si `approval_policy` está en `ask` o `deny`, los subagentes trabajan en modo de solo lectura.
- Si `approval_policy` está en `auto` y el workspace es un repo git, los subagentes mutables se ejecutan en `git worktrees` aislados y luego intentan hacer merge de vuelta al workspace principal.
- Si quieres que los subagentes también escriban ficheros o ejecuten comandos mutables, usa:

```text
/approval auto
```

Eso les permite completar subtareas de implementación en paralelo, con el mismo proveedor y el mismo workspace.

## Sesiones Gestionadas

Además del paralelismo puntual, OpenInstruct ahora soporta sesiones vivas direccionables:

```text
/session-spawn revisa auth y prepara hallazgos
/sessions
/session-send sess_... continúa con los tests y resume riesgos
/session-history sess_...
```

Notas:

- cada sesión mantiene su propia conversación y cola de trabajo
- `sessions_spawn`, `sessions_send`, `sessions_history` y `sessions_list` también están disponibles como tools internas
- `/background`, `/backgrounds` y `/waitbg` siguen existiendo, pero ahora son aliases sobre la misma capa de sesiones
- `/saved-sessions` sigue mostrando las sesiones persistidas en disco, separadas de las sesiones activas en memoria

## Planificador Maestro

Cuando no quieras repartir tareas manualmente, usa:

```text
/plan arregla el flujo de login y añade tests
```

Eso genera un plan con:

- subtareas
- dependencias entre subtareas
- intención de escritura
- rutas de escritura estimadas

Y para ejecutarlo directamente:

```text
/delegate arregla el flujo de login y añade tests
```

El planificador intenta hacer primero análisis paralelos y luego tareas de síntesis o implementación dependientes, en una línea parecida a un orquestador multiagente.

## Checkpoints y Reintentos

Cada ejecución de `/parallel` o `/delegate` ahora guarda checkpoints en `~/.openinstruct/checkpoints`:

- registra cada intento por tarea
- reintenta tareas fallidas hasta `task_retries`
- marca `blocked` cuando una tarea no puede ejecutarse porque una dependencia falló definitivamente
- deja el último run accesible desde el REPL
- permite reanudar un run y relanzar solo tareas `failed` o `blocked`, reutilizando las `success`

Comandos útiles:

```text
/retries 2
/delegate arregla login y añade tests
/checkpoints
/resume-checkpoint session-actual-dag-20260402-101530
```

También puedes inspeccionar un run concreto:

```text
/checkpoints session-actual-dag-20260402-101530
```

O reanudarlo desde CLI:

```bash
openinstruct resume-checkpoint --provider ollama --model qwen2.5-coder:14b session-actual-dag-20260402-101530
```

## Locks de Escritura

OpenInstruct usa locks compartidos para reducir conflictos:

- escrituras sobre el mismo fichero se serializan
- comandos shell mutables toman un lock global del workspace
- si el planificador detecta tareas con `write_paths` solapadas, evita lanzarlas en el mismo batch

Puedes inspeccionar locks activos en el REPL:

```text
/locks
```

## Merge Supervisor

Cuando ejecutas subtareas con escritura en paralelo o vía `/delegate`, OpenInstruct audita las mutaciones reales de cada subagente:

- registra escrituras y comandos mutables por agente
- genera diffs automáticos por fichero
- detecta handoffs cuando una tarea dependiente vuelve a tocar un fichero ya modificado
- marca conflictos cuando dos tareas independientes acaban tocando la misma ruta
- señala `out_of_scope` si un subagente escribe fuera de sus `write_paths`

Puedes inspeccionar el último informe en el REPL:

```text
/merge
```

## Background Tasks

Inspirado en el patrón de Hermes de sesiones aisladas, puedes lanzar trabajo en segundo plano:

```text
/background revisa el módulo auth y prepara hallazgos
/background genera un plan de refactor para billing
/backgrounds
/waitbg bg_1712345678901
```

Notas:

- cada background task usa una conversación separada
- comparte proveedor, modelo, workspace y lock manager
- si tu política es `auto`, puede hacer cambios; en `ask` o `deny` baja a modo seguro sin mutaciones

## Cómo funciona el agente

El modelo recibe un prompt de sistema con un protocolo JSON fijo. En cada paso devuelve:

- un resumen corto de intención
- cero o más acciones de herramienta
- una respuesta final cuando termina

OpenInstruct ejecuta las herramientas, devuelve resultados al modelo y continúa hasta completar la tarea o agotar el límite de pasos.

## Limitaciones prácticas

- La calidad real depende mucho del modelo local que uses.
- Para edición de código, modelos tipo `Qwen Coder`, `DeepSeek Coder`, `Codestral` o similares suelen ir mejor que modelos generalistas.
- No hay integración MCP ni TUI avanzada; el foco aquí es una base sólida y extensible.

## Comandos útiles

```bash
openinstruct doctor
openinstruct config show
openinstruct sessions
openinstruct run --provider auto "resume la estructura de este repo"
```
