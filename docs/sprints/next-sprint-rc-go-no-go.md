# Sprint: RC Go/No-Go And Installer Transition

## Objetivo

Cerrar la brecha final entre "release candidate técnicamente alineado" y "release candidate operable para entrega": unificar evidencia, ejecutar decisión go/no-go reproducible y abrir transición controlada al instalador desktop.

## Contexto De Entrada

Estado recibido del sprint anterior:

- Packaging RC baseline automatizado y verificable.
- Gate bloqueante en CI para release confidence + MVP gate + desktop readiness.
- Batch de robustez de handoff largo con decisión `rc_autonomy_ready`.
- Trazabilidad PRD actualizada en matriz markdown/json.

## Hipótesis De ROI

El mayor riesgo remanente no es funcionalidad nueva, sino dispersión operativa de evidencia y criterios de salida. Un sprint dedicado a decisión de release reduce riesgo de regresión y evita retrabajo antes de entrar al slice de installer completo.

## Alcance

1. Dossier de release unificado
- Consolidar evidencia RC en un único paquete auditable (JSON + MD + checklist) en `artifacts/`.
- Referenciar explícitamente: gate CI, packaging RC, confidence operacional de long handoff.

2. Gate de decisión go/no-go
- Definir política de decisión explícita con umbrales y violaciones bloqueantes.
- Automatizar evaluación de decisión desde artifacts existentes.

3. Handoff a installer transition
- Definir contrato mínimo del siguiente slice de installer (sin implementarlo completo aún): outputs esperados, prerequisitos de CI, validaciones de first-run.
- Preparar puntos de integración en packaging/workflow para activar installer mode cuando esté listo.

## Fuera De Alcance

- Rediseño visual de desktop.
- Nuevas capacidades de agente/subagentes fuera de release safety.
- Cambios de arquitectura no necesarios para go/no-go.

## Entregables

1. `artifacts/release-go-no-go.json` con:
- decision
- thresholds
- violations
- evidence_refs
- generated_at_utc

2. `artifacts/release-go-no-go.md` con resumen ejecutivo y riesgos abiertos.

3. Documento de transición installer:
- `docs/release-installer-transition.md`
- criterios de entrada/salida para activar installer en CI.

4. Actualización de CI/documentación para publicar el dossier de decisión como artifact de pipeline.

## Acceptance Criteria

1. Existe decisión determinista (`go` o `no-go`) generada por script, no manual.
2. El dossier de decisión referencia evidencia real generada por el pipeline.
3. Un fallo en criterios críticos fuerza `no-go`.
4. La transición a installer tiene contrato escrito con validaciones de first-run definidas.

## Corte Implementado 2026-05-09: Go/No-Go Dossier Automation

- Nuevo generador reproducible: `scripts/generate-release-go-no-go.py`.
- Artefactos generados localmente:
	- `artifacts/release-go-no-go.json`
	- `artifacts/release-go-no-go.md`
- Resultado actual del dossier: `decision=go`, `violations=[]`.
- Integración CI añadida en `.github/workflows/nemo-code-ci.yml`:
	- genera `artifacts/release-go-no-go.json` y `artifacts/release-go-no-go.md`
	- publica ambos como parte de `release-confidence-evidence`
- Modo CI soportado con `--allow-missing-release-bundle` para runners que no construyen bundle local en ese job.

## Corte Implementado 2026-05-09: Installer Transition Contract

- Nuevo contrato de transición a installer: `docs/release-installer-transition.md`.
- Define criterios de entrada/salida, outputs requeridos, integración CI, validación first-run y condiciones bloqueantes.
- `docs/release-packaging.md` ahora referencia explícitamente este contrato para activar el siguiente slice sin ambigüedad.

## Corte Implementado 2026-05-09: CI Installer Mode Switch (Placeholder)

- `.github/workflows/nemo-code-ci.yml` ahora incluye `workflow_dispatch` con input `installer_mode`.
- Se agrega variable global `INSTALLER_MODE` para activar flujo de transición en CI.
- Cuando `INSTALLER_MODE=true`, backend job genera evidencia placeholder:
	- `artifacts/installer/installer-transition-evidence.txt`
- Se valida presencia de evidencia y se publica artifact dedicado:
	- `installer-transition-evidence`
- Cuando `INSTALLER_MODE=false`, el pipeline mantiene comportamiento RC baseline sin cambios funcionales.

## Corte Implementado 2026-05-09: CI Installer Mode Real Build

- El workflow ya no usa evidencia placeholder para installer mode.
- Nuevo job real `installer-preview` en Windows cuando `INSTALLER_MODE=true`:
	- instala dependencias desktop
	- ejecuta `npm run tauri:build`
	- valida contrato de first-run con `tests.test_desktop_release_readiness_contract`
	- genera evidencia con checksums en `artifacts/installer/installer-evidence.json` y `.md`
	- publica artifact `installer-transition-evidence` con bundle real + evidencia.

## Corte Implementado 2026-05-09: CI Workflow Contract Guard

- Nuevo test de contrato: `tests/test_ci_workflow_contract.py`.
- Protege regresiones del pipeline verificando:
	- `workflow_dispatch` con toggle `installer_mode`
	- variable global `INSTALLER_MODE`
	- job real `installer-preview` en `windows-latest`
	- `npm run tauri:build` y evidencia `installer-evidence.json/.md`
- Validación local: `python -m unittest tests.test_ci_workflow_contract -v` (4 tests OK).

## Corte Implementado 2026-05-09: Installer No-Go Enforcement Gate

- Se agregó bloqueo explícito de installer preview si la decisión de release no es `go`.
- `installer-preview` descarga `release-confidence-evidence` y evalúa `artifacts/release-go-no-go.json`.
- Si `decision != go`, el job falla con error de política y no avanza al build installer.
- `tests/test_ci_workflow_contract.py` ahora valida este enforcement para prevenir regresiones.
- Validación local actualizada: `python -m unittest tests.test_ci_workflow_contract -v` (5 tests OK).

## Corte Implementado 2026-05-09: Real CI Run Dispatch Automation

- Nuevo script operacional: `scripts/dispatch-installer-preview.ps1`.
- Dispara `workflow_dispatch` con `installer_mode=true` vía API de GitHub.
- Espera la ejecución, monitorea estado/conclusión y valida artifacts requeridos:
	- `release-confidence-evidence`
	- `installer-transition-evidence`
- Escribe evidencia local en `artifacts/ci-installer-run-<timestamp>.json` y `.md`.
- Requiere `-Owner`, `-Repo` y token (`-Token` o `GITHUB_TOKEN`/`GH_TOKEN`).

Comando de ejecución (real run):

`pwsh -NoProfile -File scripts/dispatch-installer-preview.ps1 -Owner <org-or-user> -Repo <repo> -Ref <branch> -Token <github_pat_with_actions_scope>`


## Plan De Implementación

1. Diseñar esquema del dossier de decisión y umbrales (bloqueante).
2. Implementar generador de decisión desde artifacts actuales.
3. Generar y versionar `release-go-no-go.json/.md`.
4. Conectar publicación de dossier en CI.
5. Escribir `release-installer-transition.md` con scope, prerequisitos y gates.
6. Validar flujo completo con ejecución local de comandos de verificación.

## Verification

1. `c:/dev/dev4/.venv/Scripts/python.exe -m unittest tests.test_release_confidence_e2e tests.test_global_mvp_gate tests.test_desktop_release_readiness_contract -v`
2. `./scripts/package-release.ps1 -Version "<rc-version>"`
3. `c:/dev/dev4/.venv/Scripts/python.exe -m json.tool artifacts/long-handoff-operational-confidence.json > $null`
4. `c:/dev/dev4/.venv/Scripts/python.exe -m json.tool artifacts/release-go-no-go.json > $null`
5. `c:/dev/dev4/.venv/Scripts/python.exe scripts/generate-release-go-no-go.py --json-out artifacts/release-go-no-go.json --md-out artifacts/release-go-no-go.md`

## Riesgos

1. Falsos positivos de `go` si faltan referencias de evidencia obligatoria.
2. Acoplar demasiado la decisión a un entorno local específico.
3. Sobrecargar CI con checks redundantes; mantener solo gates críticos en camino bloqueante.

## Decisiones

- Priorizar calidad de salida de release sobre expansión de features.
- Tratar el dossier de decisión como artifact oficial de sprint/release.
- Mantener transición a installer en modo contrato + preparación de integración, sin adelantar implementación pesada en este sprint.
