# RemoteCtrl Agent Desktop

Windows desktop shell for the RemoteCtrl Agent.

## Architecture

- Tauri 2 provides the native Windows shell, child windows, tray integration and sidecar lifecycle.
- React/Vite renders the local consent, settings, privacy and activity UI.
- The packaged Python Agent Core communicates with this desktop shell over JSON Lines on standard input/output.
- The Agent opens an outbound WebSocket connection to the configured FastAPI Gateway; it does not expose a local HTTP server.

## Development

From the repository root, package the Python sidecar first, then run or build the desktop shell:

```powershell
.\scripts\package_agent_core.ps1
cd agent-desktop
npm install
npm run build
npm run desktop:dev
```

Create the Windows NSIS installer with:

```powershell
.\scripts\package_agent_desktop.ps1
```

The generated installer and checksum are copied to `release/`. See `docs/AGENT_DESKTOP.md` and `docs/E2E_TESTING.md` for architecture and verification details.