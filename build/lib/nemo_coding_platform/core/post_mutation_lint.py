from __future__ import annotations

import traceback
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class LintResult:
    file: str
    errors: str
    line_numbers: tuple[int, ...]


def lint_python_compile(filepath: str | Path, rel_path: str) -> LintResult | None:
    """Compile-check a Python file, return errors if any."""
    try:
        path = Path(filepath)
        if not path.exists():
            return None
        
        code = path.read_text(encoding="utf-8", errors="replace")
        try:
            compile(code, str(path), "exec")
            return None
        except SyntaxError as err:
            # Extract line numbers
            lineno = err.lineno or 0
            end_lineno = getattr(err, "end_lineno", lineno) or lineno
            line_numbers = tuple(range(lineno, end_lineno + 1))
            
            # Format error message similar to aider's linter
            error_msg = f"SyntaxError in {rel_path} at line {lineno}: {err.msg}"
            if err.text:
                error_msg += f"\n{err.text.rstrip()}\n" + " " * (err.offset or 0) + "^"
            
            return LintResult(file=rel_path, errors=error_msg, line_numbers=line_numbers)
        except Exception as err:
            return LintResult(file=rel_path, errors=str(err), line_numbers=())
            
    except Exception as e:
        return LintResult(file=rel_path, errors=f"Linter error: {str(e)}", line_numbers=())


def lint_changed_files(changed_files: tuple[str, ...], cwd: str | Path) -> list[LintResult]:
    """Run compile-check on all changed .py files."""
    results = []
    base_dir = Path(cwd)
    for rel_path in changed_files:
        if rel_path.endswith(".py"):
            full_path = base_dir / rel_path
            res = lint_python_compile(full_path, rel_path)
            if res:
                results.append(res)
    return results


def format_lint_evidence(results: list[LintResult]) -> str:
    """Format lint results as context for the repair prompt."""
    if not results:
        return ""
    
    lines = ["# Linting Errors Found", "The following syntax errors were detected after the last mutation:"]
    for res in results:
        lines.append(f"\n## {res.file}")
        lines.append(res.errors)
    
    lines.append("\nPlease fix these errors in the next attempt.")
    return "\n".join(lines)
