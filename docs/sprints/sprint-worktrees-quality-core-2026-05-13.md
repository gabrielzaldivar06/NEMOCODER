# Sprint — Real Git Worktrees + Quality Core Wiring
**Fecha:** 2026-05-13  
**Rama:** claudeworks  
**Alcance:** Wiring completo de git worktrees reales + Aider git-aware + merge gate API + Review tab UI

---

## Contexto

El gap analysis de sesión 4 identificó como prioridad máxima dos gaps bloqueantes:
- **GAP-01:** Quality Core / Aider no conectado al backend con git
- **GAP-02:** El handoff corre en el repo principal sin aislamiento (`git worktree add`)

Este sprint cierra ambos: cada handoff task puede ahora correr en una rama git real aislada, con Aider commiteando en ella, y un merge gate para aprobar/rechazar los cambios desde Mission Control.

---

## 1. Real Git Worktrees (`core/worktree_runtime.py`)

### Funciones nuevas

```python
def worktree_branch_name(runtime_id: str) -> str:
    return f"wt/{runtime_id}"

def create_git_worktree(repo_path: Path, worktree_path: Path, branch_name: str) -> None:
    subprocess.run(["git", "worktree", "add", str(worktree_path), "-b", branch_name],
        cwd=str(repo_path), check=True, capture_output=True, text=True)

def cleanup_git_worktree(repo_path: Path, worktree_path: Path, branch_name: str) -> None:
    # git worktree remove --force → git branch -D → git worktree prune (todos check=False)

def worktree_diff(repo_path: Path, branch_name: str, base_branch: str = "main") -> str:
    # git diff {base_branch}...{branch_name} — tres puntos (solo cambios del branch)

def merge_worktree_to_main(repo_path: Path, branch_name: str, message: str = "") -> bool:
    # git merge --no-ff branch_name -m commit_msg → retorna returncode == 0

def initialize_git_worktree_runtime(spec, repo_path, branch_name) -> WorktreeRuntimeSpec:
    # llama create_git_worktree, retorna spec sin modificar
```

### `create_worktree_runtime` actualizado

```python
def create_worktree_runtime(task_id, run_id, runtime_root, *, use_git: bool = False):
    root = Path(runtime_root).resolve()
    runtime_id = f"{runtime_slug(task_id)}-{runtime_slug(run_id)}"
    if use_git:
        repo_root = root.parent.parent  # .spacecode-runtimes/../.. = repo root
        return WorktreeRuntimeSpec(runtime_id, task_id, run_id, root, repo_root / ".worktrees" / runtime_id)
    return WorktreeRuntimeSpec(runtime_id, task_id, run_id, root, root / runtime_id)
```

**Nota:** `headless_runner.py` NO usa `create_worktree_runtime(use_git=True)` para calcular el worktree path — lo hace manualmente con `request.repo_path` para evitar dependencia de la profundidad del directorio `runtime_root`.

### Paths

- Worktrees: `<repo_root>/.worktrees/<runtime_id>/` — separado de `.spacecode-runtimes/`
- Branches: `wt/<task_slug>-<run_slug>` — determinístico, reconstruible desde metadatos del job

---

## 2. Git-Aware Aider (`core/engine_interface.py`)

### `build_git_engine_command`

Idéntica a `build_default_engine_command` pero omite tres flags que deshabilitan git en Aider:
- ~~`--no-git`~~
- ~~`--no-auto-commits`~~  
- ~~`--no-dirty-commits`~~
- ~~`--no-gitignore`~~

```python
def build_git_engine_command(profile, message_file) -> tuple[str, ...]:
    return (sys.executable, "-m", "nemo_code_runtime",
        "--model", _runtime_model_name(profile),
        "--openai-api-base", profile.base_url,
        "--openai-api-key", profile.api_key,
        "--message-file", str(message_file),
        "--yes-always", "--no-show-model-warnings", "--no-analytics")
```

### `SubprocessEngineProvider(use_git=True)`

```python
def __init__(self, command=None, cwd=".", *, use_git: bool = False):
    self.use_git = use_git
    ...

# En create_plan:
if using_default_command:
    command = build_git_engine_command(profile, message_file) if self.use_git \
              else build_default_engine_command(profile, message_file)
```

### `create_engine_provider`

```python
def create_engine_provider(provider_mode, command=None, cwd=".", *, use_git: bool = False):
    ...  # pasa use_git a SubprocessEngineProvider
```

---

## 3. Headless Runner (`core/headless_runner.py`)

### Parámetro nuevo

```python
async def execute_headless_handoff(request, ..., use_git_worktree: bool = False):
```

### Flujo cuando `use_git_worktree=True`

```python
# spec se crea normalmente (usa runtime_root estándar para obtener runtime_id)
agent_runtime = AgentRuntime.create(...)
runtime = agent_runtime.worktree

# Override del worktree_path para apuntar a .worktrees/ real
repo_root = Path(request.repo_path).resolve()
wt_path = repo_root / ".worktrees" / runtime.runtime_id
runtime = replace(runtime, worktree_path=wt_path)

# Crea el git worktree real
branch = worktree_branch_name(runtime.runtime_id)
initialize_git_worktree_runtime(runtime, repo_root, branch)
```

El provider se crea con `use_git=use_git_worktree`:
```python
provider = create_engine_provider(provider_mode, engine_command,
    cwd=Path("product/nemo_code_runtime"), use_git=use_git_worktree)
```

---

## 4. Propagación del Flag (`cli.py`)

### `long-handoff-run` y `long-handoff-continue`

```python
long_run.add_argument("--use-git-worktree", action="store_true",
    help="Isolate task in a real git worktree branch")
# → execute_long_handoff_supervisor(..., use_git_worktree=args.use_git_worktree)
```

`execute_long_handoff_supervisor` y `execute_long_handoff_continuation` ya usan `**handoff_kwargs` internamente → el flag fluye sin cambios en esas funciones.

---

## 5. Merge Gate API (`mission_control_server.py`)

### Helpers

```python
def _job_runtime_id(job) -> str:  # runtime_slug(task_id)-runtime_slug(run_id)
def _job_worktree_path(config, job) -> Path:  # config.repo_path/.worktrees/<runtime_id>
def _job_worktree_branch(job) -> str:  # wt/<runtime_id>
```

### Tres endpoints nuevos

| Método | Ruta | Función |
|--------|------|---------|
| GET | `/api/run/<job_id>/worktree-diff` | Retorna `{diff, branch, runtime_id, worktree_exists}` |
| POST | `/api/run/<job_id>/worktree-merge` | Requiere `{approved:true}`. Merge + cleanup si OK |
| POST | `/api/run/<job_id>/worktree-cleanup` | Elimina worktree y branch sin merge |

### Routing dinámico en `do_GET`/`do_POST`

```python
if route.startswith("/api/run/") and route.endswith("/worktree-diff"):
    job_id = route[len("/api/run/"): -len("/worktree-diff")].strip("/")
    self._handle(lambda _: api_worktree_diff(config, job_id, jobs), {})
    return
```

Añadido **antes** del `handlers` dict para evitar shadowing.

### `_build_command` — flag para HandoffJobManager

```python
if bool(payload.get("use_git_worktree")):
    command.append("--use-git-worktree")
```

---

## 6. Frontend — WorktreeDiffPanel (`apps/mission-control/`)

### Componente nuevo

**`src/components/WorktreeDiffPanel.tsx`**

```tsx
interface Props {
  jobId: string;
  onMerged?: () => void;
  onRejected?: () => void;
}
```

Flujo:
1. `useEffect` on mount: `GET /api/run/${jobId}/worktree-diff`
2. Muestra branch name, diff en `<pre>`, botones Approve & Merge / Reject & Remove
3. Approve: `POST /api/run/${jobId}/worktree-merge { approved: true }`
4. Reject: `POST /api/run/${jobId}/worktree-cleanup`
5. Estados: `idle | merging | merged | rejected`

Casos manejados: job vacío, loading, error de API, worktree inexistente, sin cambios.

### Wiring en `main.tsx`

```tsx
// Computed junto a selectedRun:
const selectedJob = useMemo(() =>
  state.jobs.find(j => j.task_id === selectedRun?.task_id && j.run_id === selectedRun?.run_id),
  [state.jobs, selectedRun]
);

// En workspace-main:
{runsWorkbenchTab === "review" && <WorktreeDiffPanel
  jobId={selectedJob?.job_id ?? ""}
  onMerged={() => { /* state refreshes via polling */ }}
  onRejected={() => { /* state refreshes via polling */ }}
/>}
```

**Cambio en tab rendering:**
- `runsWorkbenchTab !== "agent"` → `runsWorkbenchTab === "file"` (EditorPane solo en file tab)
- Nuevo: `runsWorkbenchTab === "review"` → WorktreeDiffPanel

### CSS nuevo (`styles.css`)

Clases: `.worktree-diff-panel`, `.worktree-diff-header`, `.branch-label`, `.diff-output`, `.diff-empty`, `.worktree-actions`, `.worktree-merge-btn`, `.worktree-reject-btn`

---

## 7. `.gitignore`

```
.worktrees/
```

Añadido junto a `.nemo-runtimes/` para que las ramas de trabajo aisladas no contaminen el árbol.

---

## 8. Tests (`tests/test_worktree_runtime.py`)

17 tests totales, todos pasan ✅

### Tests nuevos (10)

| Clase | Test |
|-------|------|
| `WorktreeBranchNameTests` | `test_branch_name_format`, `test_branch_name_passes_through_runtime_id` |
| `CreateWorktreeRuntimeUseGitTests` | `test_use_git_places_worktree_under_repo_root_dot_worktrees`, `test_use_git_false_is_unchanged` |
| `GitWorktreeFunctionTests` | `test_create_git_worktree_creates_branch_and_files`, `test_worktree_diff_shows_changes_in_worktree`, `test_worktree_diff_empty_when_no_changes`, `test_merge_worktree_to_main_applies_changes`, `test_cleanup_git_worktree_removes_dir_and_branch`, `test_initialize_git_worktree_runtime_returns_spec` |

Helper `_make_git_repo(tmp_path)`: `git init -b main`, user config, commit inicial.

---

## 9. Verificación Final

```
python -m pytest tests/test_worktree_runtime.py -v  →  17/17 ✅
npx tsc --noEmit (apps/mission-control/)             →  ✅ sin errores
```

---

## 10. Archivos Modificados

| Archivo | Cambio |
|---------|--------|
| `src/nemo_coding_platform/core/worktree_runtime.py` | 6 funciones git reales + `use_git` param en `create_worktree_runtime` |
| `src/nemo_coding_platform/core/engine_interface.py` | `build_git_engine_command`, `use_git` en `SubprocessEngineProvider` y `create_engine_provider` |
| `src/nemo_coding_platform/core/headless_runner.py` | `use_git_worktree` param, worktree override con `dataclasses.replace()` |
| `src/nemo_coding_platform/cli.py` | `--use-git-worktree` en `long-handoff-run` y `long-handoff-continue` |
| `src/nemo_coding_platform/mission_control_server.py` | 3 helpers + 3 API handlers + routing dinámico + `_build_command` flag |
| `apps/mission-control/src/components/WorktreeDiffPanel.tsx` | Componente nuevo |
| `apps/mission-control/src/styles.css` | CSS WorktreeDiffPanel |
| `apps/mission-control/src/main.tsx` | Import + `selectedJob` memo + wiring Review tab |
| `tests/test_worktree_runtime.py` | 10 tests git nuevos (3 clases) |
| `.gitignore` | `.worktrees/` añadido |

---

## 11. Gaps Cerrados vs PRD

| Gap | Estado previo | Estado ahora |
|-----|--------------|--------------|
| GAP-02 Sin worktree/sandbox | 🔴 Directo al repo principal | ✅ `git worktree add`, rama `wt/<id>`, path `.worktrees/` |
| GAP-08 Diff/Review sin merge gate real | 🟡 UI estática | ✅ WorktreeDiffPanel + API worktree-diff/merge/cleanup |
| GAP-01 Quality Core / Aider | 🟡 Aider existe pero `--no-git` siempre | ✅ `build_git_engine_command` habilita commits en worktree |

Gaps que siguen pendientes: GAP-03 (permissions), GAP-04 (Task/Run model), GAP-05 (Full Handoff), GAP-06 (NEMO full en handoff).

---

## 12. Reinicio Requerido

El servidor Python (`mission_control_server.py`) debe reiniciarse para que los 3 nuevos endpoints de merge gate estén activos.

```powershell
.\scripts\start-mvp-local.ps1
```
