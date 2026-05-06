import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.core.mutations import FileWrite, MutationPlan, QualityMutationEngine
from nemo_coding_platform.core.workspace import Workspace, snapshot_workspace


class QualityCoreTests(unittest.TestCase):
    def test_workspace_rejects_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Workspace.from_path(tmp)

            with self.assertRaises(ValueError):
                workspace.resolve_inside("../outside.txt")

    def test_mutation_requires_dry_run_and_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Workspace.from_path(tmp)
            engine = QualityMutationEngine(workspace)
            plan = MutationPlan(writes=(FileWrite("src/app.py", "print('ok')\n"),))

            with self.assertRaises(PermissionError):
                engine.apply(plan)

            dry_run = engine.dry_run(plan)

            self.assertEqual(dry_run.creates, ("src/app.py",))

    def test_approved_dry_run_plan_writes_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Workspace.from_path(tmp)
            engine = QualityMutationEngine(workspace)
            plan = MutationPlan(
                writes=(FileWrite("src/app.py", "print('ok')\n"),),
                approved=True,
                dry_run_completed=True,
            )

            applied = engine.apply(plan)

            self.assertEqual(applied, ("src/app.py",))
            self.assertEqual((Path(tmp) / "src" / "app.py").read_text(encoding="utf-8"), "print('ok')\n")

    def test_snapshot_excludes_virtualenv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".venv").mkdir()
            (root / ".venv" / "ignored.py").write_text("ignored", encoding="utf-8")
            (root / "kept.py").write_text("kept", encoding="utf-8")

            snapshot = snapshot_workspace(Workspace.from_path(root))

            self.assertEqual(snapshot.tracked_files, ("kept.py",))


if __name__ == "__main__":
    unittest.main()