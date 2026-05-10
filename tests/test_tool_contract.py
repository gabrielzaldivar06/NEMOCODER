"""Tests for core/tool_contract.py — ToolErrorCode, ToolOutput, ToolMeta."""
import pytest

from nemo_coding_platform.core.tool_contract import (
    ToolErrorCode,
    ToolMeta,
    ToolOutput,
)


class TestToolErrorCode:
    def test_all_values_are_strings(self):
        for code in ToolErrorCode:
            assert isinstance(code.value, str)

    def test_expected_codes_present(self):
        codes = {c.value for c in ToolErrorCode}
        for expected in ("not_configured", "timeout", "rate_limited", "permission_denied", "invalid_input", "tool_unavailable"):
            assert expected in codes


class TestToolOutput:
    def test_success_factory(self):
        out = ToolOutput.success(data={"x": 1})
        assert out.ok is True
        assert out.data == {"x": 1}
        assert out.error_code is None
        assert out.error_message is None

    def test_failure_factory(self):
        out = ToolOutput.failure(ToolErrorCode.TIMEOUT, "timed out")
        assert out.ok is False
        assert out.error_code == ToolErrorCode.TIMEOUT
        assert out.error_message == "timed out"

    def test_as_dict_success(self):
        out = ToolOutput.success(data=42)
        d = out.as_dict()
        assert d["ok"] is True
        assert d["data"] == 42
        assert "error_code" not in d

    def test_as_dict_failure(self):
        out = ToolOutput.failure(ToolErrorCode.RATE_LIMITED, "slow down")
        d = out.as_dict()
        assert d["ok"] is False
        assert d["error_code"] == "rate_limited"
        assert d["error_message"] == "slow down"
        assert "data" not in d

    def test_frozen(self):
        out = ToolOutput.success()
        with pytest.raises((AttributeError, TypeError)):
            out.ok = False  # type: ignore[misc]


class TestToolMeta:
    def test_default_allows_all_phases(self):
        meta = ToolMeta(slug="my_tool", description="does something")
        assert meta.allows_phase("planning") is True
        assert meta.allows_phase("execution") is True

    def test_phase_restriction(self):
        meta = ToolMeta(slug="my_tool", description="d", phases_allowed=("execution",))
        assert meta.allows_phase("execution") is True
        assert meta.allows_phase("planning") is False

    def test_risk_level_default(self):
        meta = ToolMeta(slug="x", description="y")
        assert meta.risk_level == "low"

    def test_frozen(self):
        meta = ToolMeta(slug="a", description="b")
        with pytest.raises((AttributeError, TypeError)):
            meta.slug = "z"  # type: ignore[misc]
