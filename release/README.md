# RemoteCtrl Agent release

This folder contains the artifact that testers need. Do not browse into the source folders.

## Install

1. Run `RemoteCtrlAgent-Setup.exe`.
2. Open **RemoteCtrl Agent** from the Start Menu.
3. Enter the public Gateway URL and the enrollment token created on the RemoteCtrl Web dashboard.
4. Select **Enroll** and keep the Agent open while testing.

`RemoteCtrlAgent-Setup.exe.sha256` contains the SHA-256 checksum for the installer.

## For maintainers

Run `scripts/package_agent_desktop.ps1` to build a new installer. The script automatically replaces the installer and checksum in this folder.