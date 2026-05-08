# Packaging Guide (PRD-Aligned)

This project follows PRD sequencing:

1. Stabilize headless/backend contracts.
2. Stabilize Mission Control web shell.
3. Package desktop installer later (Slice 6+).

## What to package now

Current packaging target is a local-first release bundle with:

- Python backend package artifacts (`.whl`, `.tar.gz`)
- Built Mission Control frontend (`apps/mission-control/dist`)
- Startup scripts for backend and web preview
- Manifest with artifact inventory

This matches the PRD direction: backend-first, then desktop shell.

## One-command packaging

From repo root on Windows PowerShell:

```powershell
./scripts/package-release.ps1
```

Optional custom version label:

```powershell
./scripts/package-release.ps1 -Version "0.1.0-rc1"
```

Output:

- Folder: `.release/nemocode-<bundle-version>/`
- Zip: `.release/nemocode-<bundle-version>.zip`

## Run from packaged bundle

Inside the unzipped bundle:

```powershell
./scripts/start-backend.ps1 -RepoPath "C:\path\to\repo"
```

In another shell:

```powershell
./scripts/start-web-preview.ps1 -Port 5173
```

## Desktop installer phase (next)

When moving to Slice 6 desktop delivery, package target changes to:

- Tauri or Electron desktop binary installer (`.msi`/`.exe` on Windows)
- Embedded or managed backend lifecycle
- First-run setup for model endpoint and NEMO memory path
- Signed release artifacts

Until then, use the `.zip` bundle above as the PRD-correct release artifact.
