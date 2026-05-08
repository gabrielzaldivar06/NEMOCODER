"""NEMO platform extensions for the Aider-based product fork."""

from aider.nemo_platform.autonomy import AutonomyLevel, autonomy_contract
from aider.nemo_platform.nemo_tools import NemoToolSuite, nemo_tools_for_phase
from aider.nemo_platform.permissions import PermissionAction, PermissionEvaluator, PermissionRule
from aider.nemo_platform.platform_info import platform_info, platform_info_json

__all__ = [
    "AutonomyLevel",
    "NemoToolSuite",
    "PermissionAction",
    "PermissionEvaluator",
    "PermissionRule",
    "autonomy_contract",
    "nemo_tools_for_phase",
    "platform_info",
    "platform_info_json",
]