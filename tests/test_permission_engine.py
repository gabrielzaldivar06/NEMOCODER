# tests/test_permission_engine.py
from datetime import timezone, datetime

from nemo_coding_platform.core.permission_engine import (
    PermissionAnalyzer,
    PermissionCategory,
    PermissionDecision,
    PermissionMode,
    PermissionRequest,
)


_analyzer = PermissionAnalyzer()


class TestPermissionAnalyzer:
    def test_config_write_always_asks_in_both_modes(self):
        for mode in (PermissionMode.FREEDOM, PermissionMode.RESTRICTION):
            req = _analyzer.analyze("j1", "add CI workflow to .github/workflows/", (), mode)
            assert PermissionCategory.CONFIG_FILE_WRITE in req.requires_user_approval, mode
            assert PermissionCategory.CONFIG_FILE_WRITE not in req.auto_approved, mode

    def test_shell_auto_in_freedom_ask_in_restriction(self):
        req_free = _analyzer.analyze("j2", "run pytest and fix failures", (), PermissionMode.FREEDOM)
        assert PermissionCategory.SHELL_COMMAND in req_free.auto_approved
        assert PermissionCategory.SHELL_COMMAND not in req_free.requires_user_approval

        req_restr = _analyzer.analyze("j3", "run pytest and fix failures", (), PermissionMode.RESTRICTION)
        assert PermissionCategory.SHELL_COMMAND in req_restr.requires_user_approval
        assert PermissionCategory.SHELL_COMMAND not in req_restr.auto_approved

    def test_network_auto_in_freedom_ask_in_restriction(self):
        req_free = _analyzer.analyze("j4", "fetch data from http endpoint", (), PermissionMode.FREEDOM)
        assert PermissionCategory.NETWORK_CALL in req_free.auto_approved
        assert PermissionCategory.NETWORK_CALL not in req_free.requires_user_approval

        req_restr = _analyzer.analyze("j5", "fetch data from http endpoint", (), PermissionMode.RESTRICTION)
        assert PermissionCategory.NETWORK_CALL in req_restr.requires_user_approval
        assert PermissionCategory.NETWORK_CALL not in req_restr.auto_approved

    def test_no_risky_actions_empty_requires(self):
        req = _analyzer.analyze("j6", "add docstring to calculate_total function", (), PermissionMode.RESTRICTION)
        assert req.requires_user_approval == ()
        assert req.categories == ()

    def test_file_outside_worktree_always_asks(self):
        for mode in (PermissionMode.FREEDOM, PermissionMode.RESTRICTION):
            req = _analyzer.analyze("j7", "update config", ("../../etc/passwd",), mode)
            assert PermissionCategory.FILE_WRITE_OUTSIDE_WORKTREE in req.requires_user_approval, mode

    def test_permission_request_serializable(self):
        req = _analyzer.analyze("j8", "run pytest", (), PermissionMode.RESTRICTION)
        d = req.to_dict()
        assert d["job_id"] == "j8"
        assert isinstance(d["requires_user_approval"], list)
        assert isinstance(d["auto_approved"], list)
        assert isinstance(d["rationale"], str)

    def test_permission_decision_serializable(self):
        dec = PermissionDecision(
            decided_at=datetime.now(timezone.utc).isoformat(),
            decided_by="user",
            approved=True,
            categories=(PermissionCategory.SHELL_COMMAND,),
            note="ok",
        )
        d = dec.to_dict()
        assert d["approved"] is True
        assert d["decided_by"] == "user"
        assert "shell_command" in d["categories"]
