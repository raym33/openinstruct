const cp = require("child_process");
const http = require("http");
const vscode = require("vscode");

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

class SessionTreeProvider {
  constructor(controller) {
    this.controller = controller;
    this._onDidChangeTreeData = new vscode.EventEmitter();
    this.onDidChangeTreeData = this._onDidChangeTreeData.event;
  }

  dispose() {
    this._onDidChangeTreeData.dispose();
  }

  refresh() {
    this._onDidChangeTreeData.fire();
  }

  async getChildren(element) {
    if (!element) {
      try {
        const state = await this.controller.getState();
        const items = [];
        const runtime = state.runtime;
        const runtimeItem = new vscode.TreeItem("Runtime", vscode.TreeItemCollapsibleState.None);
        runtimeItem.description = `${runtime.provider}:${runtime.model || "-"}`;
        runtimeItem.tooltip = `workdir: ${runtime.workdir}\napproval: ${runtime.approval_policy}`;
        runtimeItem.contextValue = "runtime";
        items.push(runtimeItem);
        for (const session of state.runtime.managed_sessions) {
          const label = `${session.session_id} [${session.status}]`;
          const item = new vscode.TreeItem(label, vscode.TreeItemCollapsibleState.Collapsed);
          item.description = session.title;
          item.tooltip = `${session.title}\nqueued=${session.queue_depth}`;
          item.contextValue = "managed-session";
          item.sessionId = session.session_id;
          items.push(item);
        }
        return items;
      } catch (error) {
        const item = new vscode.TreeItem("Daemon unavailable", vscode.TreeItemCollapsibleState.None);
        item.description = String(error.message || error);
        item.command = { command: "openinstruct.startDaemon", title: "Start Daemon" };
        return [item];
      }
    }

    if (element.sessionId) {
      try {
        const payload = await this.controller.request("GET", `/api/sessions/${encodeURIComponent(element.sessionId)}?limit=6`);
        return payload.queued_work.map((message) => {
          const item = new vscode.TreeItem(`${message.message_id} [${message.status}]`, vscode.TreeItemCollapsibleState.None);
          item.description = message.prompt;
          item.tooltip = message.result || message.error || message.prompt;
          item.contextValue = "managed-session-message";
          return item;
        });
      } catch (error) {
        const item = new vscode.TreeItem("Could not load history", vscode.TreeItemCollapsibleState.None);
        item.description = String(error.message || error);
        return [item];
      }
    }

    return [];
  }
}

class ChatPanel {
  constructor(controller, panel) {
    this.controller = controller;
    this.panel = panel;
    this.jobOffsets = new Map();
    this.panel.webview.options = { enableScripts: true };
    this.panel.webview.html = this.render();
    this.panel.onDidDispose(() => {
      this.controller.chatPanel = undefined;
    });
    this.panel.webview.onDidReceiveMessage(async (message) => {
      if (message.type === "send") {
        await this.controller.runInput(String(message.text || ""), this);
      } else if (message.type === "refresh") {
        await this.refresh();
      } else if (message.type === "startDaemon") {
        await this.controller.startDaemon({ revealOutput: true });
        await this.refresh();
      } else if (message.type === "stopDaemon") {
        await this.controller.stopDaemon();
        await this.refresh();
      }
    });
  }

  render() {
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <style>
    :root {
      color-scheme: light dark;
      --bg: #11232d;
      --panel: #1b3642;
      --accent: #f0c36e;
      --muted: #9bb2bf;
      --text: #f7f3e8;
      --error: #ff8670;
      --ok: #89c78f;
      --info: #8cc6ff;
      --border: rgba(240, 195, 110, 0.18);
      --shadow: rgba(0, 0, 0, 0.25);
      font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
    }
    body {
      margin: 0;
      background:
        radial-gradient(circle at top right, rgba(240, 195, 110, 0.12), transparent 32%),
        linear-gradient(180deg, #102029 0%, #162b36 100%);
      color: var(--text);
    }
    .shell {
      display: grid;
      grid-template-rows: auto 1fr auto;
      height: 100vh;
    }
    .toolbar {
      display: flex;
      gap: 8px;
      padding: 12px;
      border-bottom: 1px solid var(--border);
      background: rgba(10, 18, 24, 0.38);
      backdrop-filter: blur(10px);
    }
    button {
      border: 1px solid var(--border);
      background: #213d4b;
      color: var(--text);
      padding: 7px 12px;
      border-radius: 999px;
      cursor: pointer;
    }
    button:hover {
      background: #295060;
    }
    #status {
      padding: 10px 12px 0;
      color: var(--muted);
      font-size: 12px;
    }
    #log {
      margin: 12px;
      padding: 14px;
      overflow-y: auto;
      border-radius: 18px;
      border: 1px solid var(--border);
      background: rgba(18, 29, 35, 0.7);
      box-shadow: 0 14px 40px var(--shadow);
    }
    .entry {
      margin-bottom: 14px;
      padding-bottom: 14px;
      border-bottom: 1px solid rgba(255, 255, 255, 0.06);
    }
    .entry:last-child {
      border-bottom: 0;
      margin-bottom: 0;
      padding-bottom: 0;
    }
    .kind {
      display: inline-block;
      min-width: 72px;
      margin-bottom: 6px;
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--accent);
    }
    .kind.error {
      color: var(--error);
    }
    .kind.tool {
      color: var(--info);
    }
    .kind.result {
      color: var(--ok);
    }
    pre {
      margin: 0;
      white-space: pre-wrap;
      font-family: "SF Mono", Menlo, monospace;
      font-size: 12px;
      line-height: 1.5;
    }
    form {
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 10px;
      padding: 12px;
      border-top: 1px solid var(--border);
      background: rgba(10, 18, 24, 0.38);
    }
    textarea {
      resize: vertical;
      min-height: 72px;
      max-height: 180px;
      border-radius: 16px;
      border: 1px solid var(--border);
      background: rgba(12, 23, 29, 0.8);
      color: var(--text);
      padding: 12px;
      font-family: "SF Mono", Menlo, monospace;
    }
    .hint {
      padding: 0 12px 10px;
      color: var(--muted);
      font-size: 12px;
    }
  </style>
</head>
<body>
  <div class="shell">
    <div>
      <div class="toolbar">
        <button id="start">Start</button>
        <button id="stop">Stop</button>
        <button id="refresh">Refresh</button>
      </div>
      <div id="status">Connecting...</div>
    </div>
    <div id="log"></div>
    <div>
      <form id="composer">
        <textarea id="input" placeholder="Escribe una tarea o un slash command como /delegate arregla login"></textarea>
        <button type="submit">Send</button>
      </form>
      <div class="hint">`/` manda comandos REPL al daemon. El resto se ejecuta como prompt normal.</div>
    </div>
  </div>
  <script>
    const vscode = acquireVsCodeApi();
    const statusEl = document.getElementById("status");
    const logEl = document.getElementById("log");
    const inputEl = document.getElementById("input");

    function appendEntry(kind, text) {
      const wrapper = document.createElement("div");
      wrapper.className = "entry";
      const label = document.createElement("div");
      label.className = "kind " + kind;
      label.textContent = kind;
      const pre = document.createElement("pre");
      pre.textContent = text;
      wrapper.appendChild(label);
      wrapper.appendChild(pre);
      logEl.appendChild(wrapper);
      logEl.scrollTop = logEl.scrollHeight;
    }

    document.getElementById("composer").addEventListener("submit", (event) => {
      event.preventDefault();
      const text = inputEl.value.trim();
      if (!text) {
        return;
      }
      appendEntry("input", text);
      vscode.postMessage({ type: "send", text });
      inputEl.value = "";
    });

    document.getElementById("refresh").addEventListener("click", () => {
      vscode.postMessage({ type: "refresh" });
    });
    document.getElementById("start").addEventListener("click", () => {
      vscode.postMessage({ type: "startDaemon" });
    });
    document.getElementById("stop").addEventListener("click", () => {
      vscode.postMessage({ type: "stopDaemon" });
    });

    window.addEventListener("message", (event) => {
      const message = event.data;
      if (message.type === "status") {
        statusEl.textContent = message.text;
      }
      if (message.type === "log") {
        appendEntry(message.kind || "info", message.text || "");
      }
      if (message.type === "clear") {
        logEl.innerHTML = "";
      }
    });
  </script>
</body>
</html>`;
  }

  post(message) {
    this.panel.webview.postMessage(message);
  }

  async refresh() {
    try {
      const state = await this.controller.getState();
      const runtime = state.runtime;
      this.post({
        type: "status",
        text: `${runtime.provider}:${runtime.model || "-"} | ${runtime.approval_policy} | ${runtime.workdir}`,
      });
    } catch (error) {
      this.post({ type: "status", text: `Daemon unavailable: ${error.message || error}` });
    }
  }

  async streamJob(jobId) {
    this.jobOffsets.set(jobId, 0);
    while (true) {
      const payload = await this.controller.request("GET", `/api/jobs/${encodeURIComponent(jobId)}`);
      const offset = this.jobOffsets.get(jobId) || 0;
      const nextEvents = payload.events.slice(offset);
      for (const event of nextEvents) {
        this.post({
          type: "log",
          kind: event.kind || "info",
          text: event.message || "",
        });
      }
      this.jobOffsets.set(jobId, payload.events.length);
      if (payload.status === "completed") {
        if (payload.result) {
          this.post({ type: "log", kind: "result", text: payload.result });
        }
        this.jobOffsets.delete(jobId);
        await this.refresh();
        this.controller.sessionsProvider.refresh();
        return;
      }
      if (payload.status === "failed") {
        this.post({ type: "log", kind: "error", text: payload.error || "job failed" });
        this.jobOffsets.delete(jobId);
        await this.refresh();
        this.controller.sessionsProvider.refresh();
        return;
      }
      await sleep(800);
    }
  }
}

class OpenInstructController {
  constructor(context) {
    this.context = context;
    this.output = vscode.window.createOutputChannel("OpenInstruct");
    this.daemonProcess = undefined;
    this.chatPanel = undefined;
    this.startPromise = undefined;
    this.sessionsProvider = new SessionTreeProvider(this);
    this.refreshTimer = setInterval(() => {
      this.sessionsProvider.refresh();
      if (this.chatPanel) {
        this.chatPanel.refresh().catch(() => {});
      }
    }, 4000);
    context.subscriptions.push(
      this.output,
      this.sessionsProvider,
      { dispose: () => clearInterval(this.refreshTimer) },
      vscode.window.registerTreeDataProvider("openinstruct.sessions", this.sessionsProvider),
      vscode.commands.registerCommand("openinstruct.startDaemon", () => this.startDaemon({ revealOutput: true })),
      vscode.commands.registerCommand("openinstruct.stopDaemon", () => this.stopDaemon()),
      vscode.commands.registerCommand("openinstruct.openChat", () => this.openChat()),
      vscode.commands.registerCommand("openinstruct.refreshSessions", () => this.sessionsProvider.refresh()),
      vscode.commands.registerCommand("openinstruct.showSessionHistory", (item) => this.showSessionHistory(item))
    );
  }

  dispose() {
    clearInterval(this.refreshTimer);
    if (this.daemonProcess) {
      this.daemonProcess.kill();
      this.daemonProcess = undefined;
    }
  }

  configuration() {
    return vscode.workspace.getConfiguration("openinstruct");
  }

  workspaceFolder() {
    const folder = vscode.workspace.workspaceFolders && vscode.workspace.workspaceFolders[0];
    if (!folder) {
      throw new Error("Open a workspace folder before starting OpenInstruct.");
    }
    return folder.uri.fsPath;
  }

  endpoint() {
    const config = this.configuration();
    return {
      host: config.get("server.host", "127.0.0.1"),
      port: Number(config.get("server.port", 8765)),
    };
  }

  async request(method, route, payload) {
    const endpoint = this.endpoint();
    const body = payload ? JSON.stringify(payload) : "";
    const options = {
      hostname: endpoint.host,
      port: endpoint.port,
      path: route,
      method,
      headers: {
        "Content-Type": "application/json",
        "Content-Length": Buffer.byteLength(body),
      },
    };
    return new Promise((resolve, reject) => {
      const req = http.request(options, (res) => {
        const chunks = [];
        res.on("data", (chunk) => chunks.push(chunk));
        res.on("end", () => {
          const text = Buffer.concat(chunks).toString("utf8");
          let data = {};
          if (text.trim()) {
            try {
              data = JSON.parse(text);
            } catch (error) {
              reject(new Error(`Invalid JSON from daemon: ${text}`));
              return;
            }
          }
          if (res.statusCode >= 400) {
            reject(new Error(data.error || `${res.statusCode}`));
            return;
          }
          resolve(data);
        });
      });
      req.on("error", reject);
      if (body) {
        req.write(body);
      }
      req.end();
    });
  }

  async isHealthy() {
    try {
      await this.request("GET", "/health");
      return true;
    } catch {
      return false;
    }
  }

  buildDaemonCommand(workdir) {
    const config = this.configuration();
    const command = config.get("server.command", "openinstructd");
    const args = Array.from(config.get("server.args", []));
    const endpoint = this.endpoint();
    const provider = config.get("provider", "auto");
    const model = config.get("model", "");
    const approvalPolicy = config.get("approvalPolicy", "ask");
    const memoryBackend = config.get("memoryBackend", "none");
    const memoryPolicy = config.get("memoryPolicy", "selective");
    const maxAgents = Number(config.get("maxAgents", 3));
    const taskRetries = Number(config.get("taskRetries", 1));

    args.push("--host", endpoint.host, "--port", String(endpoint.port), "--workdir", workdir);
    if (provider) {
      args.push("--provider", provider);
    }
    if (model) {
      args.push("--model", model);
    }
    if (approvalPolicy) {
      args.push("--approval-policy", approvalPolicy);
    }
    if (memoryBackend) {
      args.push("--memory-backend", memoryBackend);
    }
    if (memoryPolicy) {
      args.push("--memory-policy", memoryPolicy);
    }
    args.push("--max-agents", String(maxAgents));
    args.push("--task-retries", String(taskRetries));
    return { command, args };
  }

  async waitForHealth(timeoutMs = 10000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      if (await this.isHealthy()) {
        return;
      }
      await sleep(250);
    }
    throw new Error("Timed out waiting for openinstructd.");
  }

  async startDaemon(options = {}) {
    if (await this.isHealthy()) {
      this.output.appendLine("Connected to existing openinstructd.");
      this.sessionsProvider.refresh();
      if (this.chatPanel) {
        await this.chatPanel.refresh();
      }
      return;
    }
    if (this.startPromise) {
      return this.startPromise;
    }
    this.startPromise = (async () => {
      const workdir = this.workspaceFolder();
      const spec = this.buildDaemonCommand(workdir);
      this.output.appendLine(`Starting openinstructd: ${spec.command} ${spec.args.join(" ")}`);
      const child = cp.spawn(spec.command, spec.args, {
        cwd: workdir,
        env: process.env,
      });
      this.daemonProcess = child;
      child.stdout.on("data", (chunk) => this.output.append(chunk.toString()));
      child.stderr.on("data", (chunk) => this.output.append(chunk.toString()));
      child.on("exit", (code, signal) => {
        this.output.appendLine(`openinstructd exited code=${code} signal=${signal}`);
        this.daemonProcess = undefined;
        this.sessionsProvider.refresh();
      });
      if (options.revealOutput) {
        this.output.show(true);
      }
      await this.waitForHealth();
      this.sessionsProvider.refresh();
      if (this.chatPanel) {
        await this.chatPanel.refresh();
      }
    })();
    try {
      await this.startPromise;
    } finally {
      this.startPromise = undefined;
    }
  }

  async stopDaemon() {
    if (this.daemonProcess) {
      this.daemonProcess.kill();
      this.daemonProcess = undefined;
    }
    this.sessionsProvider.refresh();
    if (this.chatPanel) {
      await this.chatPanel.refresh();
    }
  }

  async getState() {
    return this.request("GET", "/api/state");
  }

  async ensureDaemon() {
    if (!(await this.isHealthy())) {
      await this.startDaemon();
    }
  }

  async runInput(text, panel) {
    const input = String(text || "").trim();
    if (!input) {
      return;
    }
    await this.ensureDaemon();
    const kind = input.startsWith("/") ? "command" : "prompt";
    const job = await this.request("POST", "/api/jobs", {
      kind,
      input,
    });
    panel.post({ type: "status", text: `Job ${job.job_id} running...` });
    await panel.streamJob(job.job_id);
  }

  async openChat() {
    if (this.chatPanel) {
      this.chatPanel.panel.reveal(vscode.ViewColumn.Beside);
      await this.chatPanel.refresh();
      return;
    }
    const panel = vscode.window.createWebviewPanel(
      "openinstruct.chat",
      "OpenInstruct Chat",
      vscode.ViewColumn.Beside,
      { enableScripts: true }
    );
    this.chatPanel = new ChatPanel(this, panel);
    await this.chatPanel.refresh();
  }

  async showSessionHistory(item) {
    if (!item || !item.sessionId) {
      return;
    }
    await this.ensureDaemon();
    const payload = await this.request("GET", `/api/sessions/${encodeURIComponent(item.sessionId)}?limit=12`);
    const lines = [
      `# ${payload.session_id}`,
      "",
      `Status: ${payload.status}`,
      `Title: ${payload.title}`,
      "",
      "## Queued Work",
    ];
    for (const message of payload.queued_work) {
      lines.push(`- ${message.message_id} [${message.status}] ${message.prompt}`);
      if (message.result) {
        lines.push(`  result: ${message.result}`);
      }
      if (message.error) {
        lines.push(`  error: ${message.error}`);
      }
    }
    lines.push("", "## Conversation");
    for (const message of payload.conversation) {
      lines.push(`- ${message.role}: ${message.content}`);
    }
    const document = await vscode.workspace.openTextDocument({
      language: "markdown",
      content: lines.join("\n"),
    });
    await vscode.window.showTextDocument(document, { preview: false });
  }
}

function activate(context) {
  const controller = new OpenInstructController(context);
  context.subscriptions.push(controller);
  if (controller.configuration().get("autoStart", true) && vscode.workspace.workspaceFolders?.length) {
    controller.startDaemon().catch((error) => {
      controller.output.appendLine(`Failed to autostart openinstructd: ${error.message || error}`);
    });
  }
}

function deactivate() {
  return undefined;
}

module.exports = {
  activate,
  deactivate,
};
