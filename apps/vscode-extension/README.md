# OpenInstruct Local Extension

Extensión ligera para `Code - OSS` o `VS Code` que:

- arranca `openinstructd`
- muestra un panel de chat local
- lista sesiones gestionadas y subagentes activos
- permite lanzar prompts o slash commands contra el daemon

## Desarrollo rápido

Abre esta carpeta en un host con `Code - OSS` o `VS Code`, pulsa `F5` y arranca la extension host.

La extensión intenta lanzar:

```bash
openinstructd --host 127.0.0.1 --port 8765 --workdir <workspace>
```

Puedes cambiarlo con settings:

- `openinstruct.server.command`
- `openinstruct.server.args`
- `openinstruct.provider`
- `openinstruct.model`
- `openinstruct.approvalPolicy`
- `openinstruct.memoryBackend`

## Packaging

Sin build step. El entrypoint es [extension.js](/Users/c/Desktop/carpeta%20sin%20ti%CC%81tulo%202/openinstruct/apps/vscode-extension/extension.js).
