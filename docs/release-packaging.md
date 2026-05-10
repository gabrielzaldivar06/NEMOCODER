# Packaging Guide (Global PRD Finalization)

This guide defines the release-candidate packaging baseline used during Global PRD Finalization.

## Packaging Stages

1. RC Baseline (current): reproducible release bundle with backend artifacts, Mission Control web, desktop shell build evidence, launcher scripts, manifest, integrity report, and release checklist.
2. Installer Target (next): desktop installer output (`.msi`/`.exe`) with managed backend lifecycle and signed artifacts.

## RC Baseline Scope

The current packaging target is a local-first release bundle containing:

- Python backend artifacts (`.whl`, `.tar.gz`)
- Built Mission Control frontend (`apps/mission-control/dist`)
- Built desktop shell web artifacts (`apps/nemo-desktop/dist`) as release evidence
- Startup scripts for backend and web preview
- `manifest.json` with artifact inventory
- `integrity.json` with SHA-256 hashes for every packaged file
- `release-checklist.md` with machine-assisted verification points

This baseline keeps delivery reproducible while installer automation is finalized.

## One-Command Packaging

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

## Run from Packaged Bundle

Inside the unzipped bundle:

```powershell
./scripts/start-backend.ps1 -RepoPath "C:\path\to\repo"
```

In another shell:

```powershell
./scripts/start-web-preview.ps1 -Port 5173
```

## RC Verification Checklist

The generated `release-checklist.md` should confirm:

1. Backend wheel and sdist exist.
2. Mission Control and desktop shell builds are present.
3. Launch scripts were generated.
4. Integrity report was generated.
5. Bundle zip exists.

## Installer Target (Next)

When moving to installer delivery, packaging must include:

- Tauri desktop installer (`.msi`/`.exe` on Windows)
- Backend lifecycle managed by the desktop app
- First-run setup validation for model endpoint and NEMO path
- Signed release artifacts

Installer transition gating and first-run validation contract:

- `docs/release-installer-transition.md`

Until installer automation is stable in CI, the RC baseline bundle above is the required release artifact.
