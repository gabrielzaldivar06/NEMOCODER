import tempfile
import unittest
from pathlib import Path

from nemo_coding_platform.core.mutations import FileWrite, MutationPlan, QualityMutationEngine
from nemo_coding_platform.core.quality import ValidationStatus
from nemo_coding_platform.core.workspace import Workspace


class QualityCoreContractTests(unittest.TestCase):
    def test_cannot_apply_without_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Workspace.from_path(tmp)
            engine = QualityMutationEngine(workspace)
            plan = MutationPlan(writes=(FileWrite("file.txt", "hello"),), approved=True, dry_run_completed=False)

            with self.assertRaises(PermissionError):
                engine.apply(plan)

    def test_cannot_apply_without_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Workspace.from_path(tmp)
            engine = QualityMutationEngine(workspace)
            plan = MutationPlan(writes=(FileWrite("file.txt", "hello"),), approved=False, dry_run_completed=True)

            with self.assertRaises(PermissionError):
                engine.apply(plan)

    def test_path_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Workspace.from_path(tmp)
            engine = QualityMutationEngine(workspace)
            plan = MutationPlan(writes=(FileWrite("..\\escape.txt", "bad"),), approved=True, dry_run_completed=True)

            with self.assertRaises(ValueError):
                engine.dry_run(plan)

    def test_dry_run_reports_creates_and_updates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "existing.txt").write_text("old", encoding="utf-8")

            workspace = Workspace.from_path(root)
            engine = QualityMutationEngine(workspace)
            plan = MutationPlan(
                writes=(
                    FileWrite("existing.txt", "new"),
                    FileWrite("new.txt", "created"),
                ),
                approved=False,
                dry_run_completed=False,
            )

            dry_run = engine.dry_run(plan)
            self.assertEqual(dry_run.files, ("existing.txt", "new.txt"))
            self.assertEqual(dry_run.updates, ("existing.txt",))
            self.assertEqual(dry_run.creates, ("new.txt",))

    def test_validation_status_enum_covers_contract_states(self) -> None:
        values = {item.value for item in ValidationStatus}
        self.assertEqual(values, {"pass", "fail", "skipped"})


if __name__ == "__main__":
    unittest.main()