import unittest

from nemo_coding_platform.core.repair import RepairBudget, RepairPlan


class RepairTests(unittest.TestCase):
    def test_repair_plan_records_until_budget_exhausted(self) -> None:
        plan = RepairPlan(RepairBudget(1))

        self.assertTrue(plan.can_record_attempt())
        plan = plan.next_attempt("validation_failed", "retry")

        self.assertTrue(plan.exhausted)
        self.assertIn("1/1", plan.summary())
        with self.assertRaises(RuntimeError):
            plan.next_attempt("again", "retry")

    def test_repair_budget_rejects_negative(self) -> None:
        with self.assertRaises(ValueError):
            RepairBudget(-1)


if __name__ == "__main__":
    unittest.main()