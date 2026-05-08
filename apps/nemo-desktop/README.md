# Nemocode Desktop

Local-first AI coding agent with Spec-Driven Development, packaged as a desktop application using Tauri and React.

## Overview

**Nemocode Desktop** is the graphical interface for the Nemocode AI assistant. It provides:

- **Repository Picker**: Choose which repository to operate on
- **Task Workspace**: Define and manage coding tasks
- **Approval Queue**: Review and approve agent-proposed changes before they're applied
- **Artifact Timeline**: Visualize the history of generated artifacts and changes
- **NEMO Memory Trace**: View and interact with the persistent memory system
- **Model Settings**: Configure LM Studio integration and model parameters

The desktop app is built with:
- **Frontend**: React + TypeScript + Vite
- **Desktop Shell**: Tauri (lightweight, Rust-backed)
- **Backend Bridge**: Tauri commands for backend lifecycle, health, and settings persistence

## Prerequisites

### System Requirements
- **Node.js**: 18.0+ (for frontend development)
- **Rust**: 1.70+ (for Tauri compilation)
- **Tauri CLI**: Install with `npm install -g @tauri-apps/cli`

### Running Services
- **Mission Control Backend**: Must be running on `http://localhost:8787`
- **LM Studio**: For local model inference (optional but recommended)

## Installation

### 1. Install Dependencies

```bash
# From the nemo-desktop directory
npm install
```

### 2. Set Up Tauri (First Time Only)

```bash
npm run tauri -- init
```

This sets up Tauri CLI and initializes the development environment.

## Development

### Start Development Server

```bash
# Frontend dev server + Tauri window
npm run tauri:dev
```

This will:
1. Start Vite dev server on `http://localhost:5173`
2. Open the Tauri desktop window
3. Hot-reload frontend changes in real time

### Build for Production

```bash
npm run tauri:build
```

This will:
1. Build the frontend (TypeScript + React → dist/)
2. Compile the Tauri app (Rust backend)
3. Generate platform-specific installers (Windows .msi, macOS .dmg, Linux .AppImage)

## Architecture

### Frontend (src/)
- **main.tsx**: Entry point for the desktop wrapper, manages backend startup and shows the embedded Mission Control shell
- **index.css**: Application styling and layout
- **index.html**: HTML template

Key Features:
- Starts or reconnects to the local Mission Control backend
- Performs health checks on startup and periodic heartbeat checks
- Embeds the active Mission Control web UI from `http://localhost:8787`

### Backend (src-tauri/src/)
- **main.rs**: Tauri backend with command handlers

Key Commands:
- `health_check()`: Returns app health status
- `get_settings()`: Retrieves persistent settings
- `save_settings(settings)`: Persists settings to disk
- `proxy_backend_request(path, method, body)`: Optional helper to forward HTTP requests to localhost:8787

### Configuration
- **tauri.conf.json**: Window size, app metadata, security settings
- **vite.config.ts**: Frontend build configuration, dev server proxy
- **package.json**: Node dependencies and build scripts
- **src-tauri/Cargo.toml**: Rust dependencies

## Backend Integration

### Desktop Runtime Flow

The desktop wrapper talks to the backend in two ways:

```
Desktop React wrapper
  ↓ invoke()
Tauri commands (start/stop/status/settings)
  ↓
Mission Control Backend (localhost:8787)

Embedded iframe
  ↓ direct HTTP
Mission Control Backend (localhost:8787)
```

Example flows:
- **App Startup**: `loadSettings()` → optional `start_backend()` → `query_backend_status()`
- **Review Gate**: `POST /api/review` → Submit changes for approval
- **Apply Changes**: `POST /api/apply` → Accept and apply approved changes
- **Rollback**: `POST /api/rollback` → Undo recent changes
- **State**: `GET /api/state` → Fetch current agent state

### Settings Persistence

Application settings are stored in platform-specific directories:
- **Windows**: `%APPDATA%\nemocode\settings.json`
- **macOS**: `~/Library/Application Support/nemocode/settings.json`
- **Linux**: `~/.config/nemocode/settings.json`

Current persisted fields:
- `backend_port`
- `lm_studio_url`
- `nemo_database_path`
- `auto_start_backend`
- `theme`

## Debugging

### Enable Debug Logging

```bash
# macOS/Linux
RUST_LOG=debug npm run tauri:dev

# Windows
$env:RUST_LOG = 'debug'
npm run tauri:dev
```

### Check Backend Connection

From the Mission Control iframe or any browser pointing at the bridge, run:

```javascript
fetch('http://localhost:8787/api/health').then(r => r.json()).then(console.log)
```

### Inspect Frontend Errors

Use DevTools for the desktop wrapper to inspect startup/health behavior, and DevTools inside the embedded Mission Control UI to inspect operational network requests.

## Troubleshooting

### Backend Connection Fails
- Ensure Mission Control is running: `python -m nemo_coding_platform.mission_control` or let the desktop wrapper auto-start it
- Check backend URL: Should be `http://localhost:8787`
- Verify no firewall blocking localhost connections

### Settings Do Not Persist
- Verify the platform config directory is writable
- Delete `%APPDATA%\nemocode\settings.json` on Windows if the file was corrupted
- Restart the desktop shell after changing settings to confirm they reload correctly

### Tauri Build Fails
- Ensure Rust is installed: `rustc --version`
- Update Rust: `rustup update`
- Clear build cache: `rm -rf src-tauri/target`

### Frontend Hot Reload Doesn't Work
- Restart Vite dev server: `npm run tauri:dev`
- Clear browser cache: Open DevTools → Application → Clear storage

## Deep Linking

Nemocode Desktop can be launched with deep links:

```bash
# Open a specific repository
nemocode://open-repo?path=/path/to/repo

# Start a task
nemocode://start-task?name=my-task
```

## Platform-Specific Notes

### Windows
- Requires Visual Studio or C++ build tools (included with Node.js setup)
- Installer format: .msi

### macOS
- Code signing required for App Store distribution (optional)
- Installer format: .dmg

### Linux
- Requires GTK 3.6+ (usually pre-installed on modern distributions)
- Installer format: .AppImage

## Performance

- **Startup Time**: < 2 seconds (Tauri is fast)
- **Memory Usage**: 100-150 MB with embedded Mission Control shell
- **Frontend Responsiveness**: UI remains responsive because execution stays in the Mission Control backend

## Further Reading

- [Tauri Documentation](https://tauri.app/v1/docs/getting-started/intro)
- [Vite Documentation](https://vitejs.dev/)
- [React Documentation](https://react.dev/)
- [Mission Control API Reference](../mission-control/API.md)

## License

Same as parent Nemocode project.
