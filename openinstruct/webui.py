from __future__ import annotations

import json
from typing import Any, Dict


def render_mobile_ui(initial_state: Dict[str, Any] | None = None) -> str:
    boot_json = json.dumps(initial_state or {}, ensure_ascii=True)
    return f"""<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <title>OpenInstruct Mobile</title>
  <style>
    :root {{
      --bg: #f6f1e8;
      --panel: rgba(255, 252, 246, 0.88);
      --panel-strong: #fffaf2;
      --ink: #1d2430;
      --muted: #5d6675;
      --line: rgba(29, 36, 48, 0.12);
      --brand: #1f5eff;
      --brand-soft: rgba(31, 94, 255, 0.12);
      --ok: #0d8f55;
      --warn: #b26a00;
      --err: #b42318;
      --shadow: 0 20px 60px rgba(27, 39, 62, 0.12);
      --radius: 20px;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; min-height: 100%; }}
    body {{
      background:
        radial-gradient(circle at top left, rgba(31, 94, 255, 0.12), transparent 30%),
        radial-gradient(circle at bottom right, rgba(214, 152, 57, 0.16), transparent 28%),
        var(--bg);
      color: var(--ink);
      font-family: "Avenir Next", "Segoe UI", sans-serif;
      line-height: 1.45;
    }}
    .app {{
      width: min(1080px, calc(100vw - 24px));
      margin: 0 auto;
      padding: max(16px, env(safe-area-inset-top)) 0 max(20px, env(safe-area-inset-bottom));
    }}
    .hero, .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      backdrop-filter: blur(14px);
    }}
    .hero {{
      padding: 18px;
      margin-bottom: 14px;
    }}
    .eyebrow {{
      font-size: 12px;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--muted);
      margin-bottom: 10px;
    }}
    h1, h2 {{
      margin: 0;
      font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
      font-weight: 600;
      letter-spacing: -0.02em;
    }}
    h1 {{ font-size: clamp(28px, 8vw, 42px); }}
    h2 {{ font-size: 20px; margin-bottom: 10px; }}
    .subhead {{
      color: var(--muted);
      margin-top: 8px;
      max-width: 62ch;
    }}
    .runtime {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin-top: 14px;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      border-radius: 999px;
      padding: 7px 12px;
      background: var(--panel-strong);
      border: 1px solid var(--line);
      font-size: 13px;
    }}
    .pill strong {{ font-weight: 600; }}
    .pill.ok {{ color: var(--ok); }}
    .pill.warn {{ color: var(--warn); }}
    .pill.err {{ color: var(--err); }}
    .card {{
      padding: 16px;
      margin-bottom: 14px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 14px;
    }}
    .stack {{
      display: grid;
      gap: 10px;
    }}
    .row {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }}
    .segmented {{
      display: inline-grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 6px;
      width: 100%;
      margin-bottom: 10px;
    }}
    .segmented button {{
      border: 1px solid var(--line);
      background: transparent;
      color: var(--muted);
    }}
    .segmented button.active {{
      background: var(--brand);
      color: white;
      border-color: var(--brand);
    }}
    button, textarea, input {{
      font: inherit;
    }}
    button {{
      appearance: none;
      border: 0;
      border-radius: 16px;
      padding: 12px 14px;
      background: var(--brand);
      color: white;
      cursor: pointer;
      transition: transform 120ms ease, opacity 120ms ease;
    }}
    button.secondary {{
      background: var(--brand-soft);
      color: var(--brand);
    }}
    button.ghost {{
      background: transparent;
      color: var(--muted);
      border: 1px solid var(--line);
    }}
    button:disabled {{
      opacity: 0.45;
      cursor: not-allowed;
    }}
    textarea, input[type="text"] {{
      width: 100%;
      border-radius: 16px;
      border: 1px solid var(--line);
      background: rgba(255, 255, 255, 0.72);
      color: var(--ink);
      padding: 14px;
      min-height: 120px;
      resize: vertical;
    }}
    .mini-input {{
      min-height: 88px;
    }}
    .hint {{
      color: var(--muted);
      font-size: 13px;
    }}
    .list {{
      display: grid;
      gap: 8px;
      max-height: 340px;
      overflow: auto;
      padding-right: 4px;
    }}
    .list button {{
      text-align: left;
      background: rgba(255, 255, 255, 0.72);
      color: var(--ink);
      border: 1px solid var(--line);
      padding: 12px;
    }}
    .list button.active {{
      border-color: var(--brand);
      background: rgba(31, 94, 255, 0.08);
    }}
    .item-title {{
      display: block;
      font-weight: 600;
      margin-bottom: 4px;
    }}
    .item-meta {{
      display: block;
      font-size: 12px;
      color: var(--muted);
    }}
    .detail {{
      white-space: pre-wrap;
      word-break: break-word;
      min-height: 240px;
      max-height: 60vh;
      overflow: auto;
      padding: 14px;
      border-radius: 18px;
      background: rgba(19, 26, 38, 0.92);
      color: #f1f5ff;
      font-family: "SFMono-Regular", "Menlo", monospace;
      font-size: 12px;
      line-height: 1.55;
    }}
    .detail.empty {{
      background: rgba(255, 255, 255, 0.56);
      color: var(--muted);
      font-family: "Avenir Next", "Segoe UI", sans-serif;
    }}
    .status-bar {{
      margin-top: 10px;
      font-size: 13px;
      color: var(--muted);
    }}
    .toggle {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 14px;
      color: var(--muted);
    }}
    @media (min-width: 960px) {{
      .grid {{
        grid-template-columns: minmax(280px, 340px) minmax(280px, 340px) minmax(0, 1fr);
        align-items: start;
      }}
      .composer-card {{
        grid-column: 1 / -1;
      }}
    }}
  </style>
</head>
<body>
  <main class="app">
    <section class="hero">
      <div class="eyebrow">OpenInstruct / Mobile</div>
      <h1>Controla el daemon desde el iPhone</h1>
      <p class="subhead">Prompt normal, slash commands, sesiones en background y seguimiento de jobs, todo sobre la misma API local de <code>openinstructd</code>.</p>
      <div class="runtime" id="runtimePills"></div>
      <div class="status-bar" id="statusBar">Conectando con el daemon...</div>
    </section>

    <section class="card composer-card">
      <h2>Composer</h2>
      <div class="segmented">
        <button type="button" data-kind="prompt" class="active">Prompt</button>
        <button type="button" data-kind="command">Slash command</button>
      </div>
      <textarea id="jobInput" placeholder="Escribe una tarea o un comando tipo /kb-ask, /delegate, /review..."></textarea>
      <div class="row">
        <button type="button" id="runJobButton">Enviar al daemon</button>
        <button type="button" class="secondary" id="spawnSessionButton">Crear sesion</button>
        <label class="toggle"><input type="checkbox" id="sessionWrite"> sesion con write</label>
      </div>
      <div class="hint">En modo command manda algo como <code>/status</code> o <code>/kb-ask pregunta...</code>. En modo prompt ejecuta una tarea normal del agente.</div>
    </section>

    <section class="grid">
      <section class="card stack">
        <div class="row" style="justify-content:space-between">
          <h2>Jobs</h2>
          <button type="button" class="ghost" id="refreshJobsButton">Refrescar</button>
        </div>
        <div class="list" id="jobsList"></div>
      </section>

      <section class="card stack">
        <div class="row" style="justify-content:space-between">
          <h2>Sesiones</h2>
          <button type="button" class="ghost" id="refreshSessionsButton">Refrescar</button>
        </div>
        <div class="list" id="sessionsList"></div>
        <textarea id="sessionInput" class="mini-input" placeholder="Continua una sesion seleccionada..."></textarea>
        <button type="button" class="secondary" id="sendSessionButton" disabled>Enviar a sesion</button>
      </section>

      <section class="card stack">
        <h2 id="detailTitle">Detalle</h2>
        <pre id="detailOutput" class="detail empty">Selecciona un job o una sesion para ver su salida aqui.</pre>
      </section>
    </section>
  </main>

  <script>
    const boot = {boot_json};
    const state = {{
      mode: "prompt",
      selectedJobId: "",
      selectedSessionId: "",
      activeDetail: "",
      lastState: boot,
      pollHandle: null
    }};

    const qs = (selector) => document.querySelector(selector);
    const qsa = (selector) => Array.from(document.querySelectorAll(selector));

    async function fetchJson(path, init) {{
      const response = await fetch(path, {{
        headers: {{
          "Accept": "application/json",
          ...(init && init.body ? {{ "Content-Type": "application/json" }} : {{}})
        }},
        ...init
      }});
      const text = await response.text();
      let payload = {{}};
      if (text) {{
        try {{
          payload = JSON.parse(text);
        }} catch (error) {{
          throw new Error(`Respuesta no JSON desde ${{path}}: ${{text.slice(0, 280)}}`);
        }}
      }}
      if (!response.ok) {{
        throw new Error(payload.error || `HTTP ${{response.status}}`);
      }}
      return payload;
    }}

    function formatTime(epochSeconds) {{
      if (!epochSeconds) return "ahora";
      const date = new Date(epochSeconds * 1000);
      return date.toLocaleTimeString([], {{ hour: "2-digit", minute: "2-digit" }});
    }}

    function clip(value, limit = 120) {{
      const text = String(value || "").trim().replace(/\\s+/g, " ");
      if (!text) return "(vacio)";
      return text.length > limit ? `${{text.slice(0, limit - 1)}}…` : text;
    }}

    function setStatus(message, tone = "ok") {{
      const node = qs("#statusBar");
      node.textContent = message;
      node.style.color = tone === "error" ? "var(--err)" : tone === "warn" ? "var(--warn)" : "var(--muted)";
    }}

    function renderRuntime(payload) {{
      const runtime = (payload && payload.runtime) || {{}};
      const pills = [
        ["estado", payload && payload.status ? payload.status : "desconocido", payload && payload.status === "ok" ? "ok" : "warn"],
        ["provider", runtime.provider || "-", ""],
        ["model", runtime.model || "-", ""],
        ["approval", runtime.approval_policy || "-", runtime.approval_policy === "auto" ? "warn" : ""],
        ["workdir", clip(runtime.workdir || "-", 36), ""],
        ["jobs", String((payload && payload.job_count) || 0), ""],
        ["sessions", String(((runtime.managed_sessions) || []).length), ""]
      ];
      qs("#runtimePills").innerHTML = pills.map(([label, value, tone]) => `
        <span class="pill ${{tone}}">
          <strong>${{label}}</strong>
          <span>${{value}}</span>
        </span>
      `).join("");
    }}

    function renderJobs(payload) {{
      const jobs = (payload && payload.jobs) || [];
      const container = qs("#jobsList");
      if (!jobs.length) {{
        container.innerHTML = `<div class="hint">Todavia no hay jobs.</div>`;
        return;
      }}
      if (!state.selectedJobId) {{
        state.selectedJobId = jobs[jobs.length - 1].job_id;
        state.activeDetail = "job";
      }}
      container.innerHTML = jobs.slice().reverse().map((job) => `
        <button type="button" data-job-id="${{job.job_id}}" class="${{job.job_id === state.selectedJobId && state.activeDetail === "job" ? "active" : ""}}">
          <span class="item-title">${{clip(job.input_text, 68)}}</span>
          <span class="item-meta">${{job.kind}} · ${{job.status}} · ${{formatTime(job.updated_at || job.created_at)}}</span>
        </button>
      `).join("");
      container.querySelectorAll("[data-job-id]").forEach((button) => {{
        button.addEventListener("click", () => {{
          state.selectedJobId = button.dataset.jobId || "";
          state.activeDetail = "job";
          loadSelectedDetail();
          renderJobs(payload);
        }});
      }});
    }}

    function renderSessions(payload) {{
      const sessions = (payload && payload.sessions) || [];
      const container = qs("#sessionsList");
      if (!sessions.length) {{
        container.innerHTML = `<div class="hint">No hay sesiones gestionadas activas.</div>`;
        qs("#sendSessionButton").disabled = true;
        return;
      }}
      if (!state.selectedSessionId) {{
        state.selectedSessionId = sessions[sessions.length - 1].session_id;
      }}
      container.innerHTML = sessions.slice().reverse().map((session) => `
        <button type="button" data-session-id="${{session.session_id}}" class="${{session.session_id === state.selectedSessionId && state.activeDetail === "session" ? "active" : ""}}">
          <span class="item-title">${{clip(session.title || session.session_id, 68)}}</span>
          <span class="item-meta">${{session.status}} · cola=${{session.queue_depth}} · ${{formatTime(session.updated_at || session.created_at)}}</span>
        </button>
      `).join("");
      container.querySelectorAll("[data-session-id]").forEach((button) => {{
        button.addEventListener("click", () => {{
          state.selectedSessionId = button.dataset.sessionId || "";
          state.activeDetail = "session";
          loadSelectedDetail();
          renderSessions(payload);
        }});
      }});
      qs("#sendSessionButton").disabled = !state.selectedSessionId;
    }}

    function setDetail(title, content, empty = false) {{
      qs("#detailTitle").textContent = title;
      const detail = qs("#detailOutput");
      detail.textContent = content;
      detail.classList.toggle("empty", empty);
    }}

    async function loadJob(jobId) {{
      const payload = await fetchJson(`/api/jobs/${{encodeURIComponent(jobId)}}`);
      const lines = [
        `job: ${{payload.job_id}}`,
        `kind: ${{payload.kind}}`,
        `status: ${{payload.status}}`,
        `created: ${{formatTime(payload.created_at)}}`,
        `updated: ${{formatTime(payload.updated_at)}}`,
        ""
      ];
      if (payload.result) {{
        lines.push("result:");
        lines.push(payload.result);
        lines.push("");
      }}
      if (payload.error) {{
        lines.push("error:");
        lines.push(payload.error);
        lines.push("");
      }}
      if (payload.events && payload.events.length) {{
        lines.push("events:");
        for (const event of payload.events.slice(-30)) {{
          lines.push(`[${{event.kind}}] ${{event.message}}`);
        }}
      }}
      setDetail("Detalle del job", lines.join("\\n"), false);
    }}

    async function loadSession(sessionId) {{
      const payload = await fetchJson(`/api/sessions/${{encodeURIComponent(sessionId)}}?limit=12`);
      const lines = [
        `session: ${{payload.session_id}}`,
        `status: ${{payload.status}}`,
        `queue_depth: ${{payload.queue_depth}}`,
        ""
      ];
      if (payload.queued_work && payload.queued_work.length) {{
        lines.push("queued_work:");
        for (const item of payload.queued_work.slice(-16)) {{
          lines.push(`- [$${{item.status}}] ${{item.prompt}}`.replace("$", ""));
        }}
        lines.push("");
      }}
      if (payload.conversation && payload.conversation.length) {{
        lines.push("conversation:");
        for (const item of payload.conversation.slice(-24)) {{
          lines.push(`${{item.role}}: ${{item.content}}`);
          lines.push("");
        }}
      }}
      setDetail("Detalle de la sesion", lines.join("\\n"), false);
    }}

    async function loadSelectedDetail() {{
      try {{
        if (state.activeDetail === "session" && state.selectedSessionId) {{
          await loadSession(state.selectedSessionId);
          return;
        }}
        if (state.selectedJobId) {{
          state.activeDetail = "job";
          await loadJob(state.selectedJobId);
          return;
        }}
        setDetail("Detalle", "Selecciona un job o una sesion para ver su salida aqui.", true);
      }} catch (error) {{
        setDetail("Detalle", error.message || String(error), false);
        setStatus(error.message || String(error), "error");
      }}
    }}

    async function refresh() {{
      try {{
        const [statePayload, sessionsPayload] = await Promise.all([
          fetchJson("/api/state"),
          fetchJson("/api/sessions")
        ]);
        state.lastState = statePayload;
        renderRuntime(statePayload);
        renderJobs(statePayload);
        renderSessions(sessionsPayload);
        setStatus(`Daemon listo · ${{statePayload.job_count || 0}} jobs · ${{
          ((sessionsPayload && sessionsPayload.sessions) || []).length
        }} sesiones`);
        await loadSelectedDetail();
      }} catch (error) {{
        setStatus(error.message || String(error), "error");
      }}
    }}

    async function submitJob() {{
      const input = qs("#jobInput").value.trim();
      if (!input) {{
        setStatus("Escribe algo antes de enviar.", "warn");
        return;
      }}
      try {{
        const job = await fetchJson("/api/jobs", {{
          method: "POST",
          body: JSON.stringify({{
            kind: state.mode,
            input
          }})
        }});
        qs("#jobInput").value = "";
        state.selectedJobId = job.job_id;
        state.activeDetail = "job";
        setStatus(`Job creado: ${{job.job_id}}`);
        await refresh();
      }} catch (error) {{
        setStatus(error.message || String(error), "error");
      }}
    }}

    async function spawnSession() {{
      const prompt = qs("#jobInput").value.trim();
      if (!prompt) {{
        setStatus("Necesitas un prompt para crear la sesion.", "warn");
        return;
      }}
      try {{
        const payload = await fetchJson("/api/sessions", {{
          method: "POST",
          body: JSON.stringify({{
            prompt,
            write: qs("#sessionWrite").checked
          }})
        }});
        qs("#jobInput").value = "";
        state.selectedSessionId = payload.session_id;
        state.activeDetail = "session";
        setStatus(`Sesion creada: ${{payload.session_id}}`);
        await refresh();
      }} catch (error) {{
        setStatus(error.message || String(error), "error");
      }}
    }}

    async function sendSessionMessage() {{
      const prompt = qs("#sessionInput").value.trim();
      if (!state.selectedSessionId) {{
        setStatus("Selecciona una sesion primero.", "warn");
        return;
      }}
      if (!prompt) {{
        setStatus("Escribe algo para enviar a la sesion.", "warn");
        return;
      }}
      try {{
        await fetchJson(`/api/sessions/${{encodeURIComponent(state.selectedSessionId)}}/messages`, {{
          method: "POST",
          body: JSON.stringify({{ prompt }})
        }});
        qs("#sessionInput").value = "";
        state.activeDetail = "session";
        setStatus(`Mensaje enviado a ${{state.selectedSessionId}}`);
        await refresh();
      }} catch (error) {{
        setStatus(error.message || String(error), "error");
      }}
    }}

    function wireComposer() {{
      qsa("[data-kind]").forEach((button) => {{
        button.addEventListener("click", () => {{
          state.mode = button.dataset.kind || "prompt";
          qsa("[data-kind]").forEach((candidate) => candidate.classList.toggle("active", candidate === button));
        }});
      }});
      qs("#runJobButton").addEventListener("click", submitJob);
      qs("#spawnSessionButton").addEventListener("click", spawnSession);
      qs("#sendSessionButton").addEventListener("click", sendSessionMessage);
      qs("#refreshJobsButton").addEventListener("click", refresh);
      qs("#refreshSessionsButton").addEventListener("click", refresh);
    }}

    function bootstrap() {{
      wireComposer();
      if (boot && Object.keys(boot).length) {{
        renderRuntime(boot);
        renderJobs(boot);
      }}
      refresh();
      state.pollHandle = window.setInterval(refresh, 2500);
    }}

    bootstrap();
  </script>
</body>
</html>
"""
