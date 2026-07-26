# Agent Desktop Changelog

## 0.2.0

- Replaced the PySide6/Qt desktop interface with Tauri 2, React/Vite, Tailwind CSS and shadcn/ui components.
- Moved Agent UI communication to a JSON Lines Python sidecar bridge; no local HTTP port is introduced.
- Added Overview, Access & Privacy, Activity and Settings pages, a native tray menu, first-run enrollment, themes and modeless approval windows.
- Bundled the Python Agent Core with webcam dependencies in the Windows NSIS distribution.
- Removed the legacy PySide6 dependency, Qt UI test, old spec file and the prior executable packaging path.