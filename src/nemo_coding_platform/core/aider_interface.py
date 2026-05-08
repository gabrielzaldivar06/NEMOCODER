from __future__ import annotations

from nemo_coding_platform.core.engine_interface import ENGINE_MESSAGE_FILE, EngineProvider, FakeEngineProvider, MutationRequest, MutationResult, SubprocessEngineProvider, TokenUsage, apply_mutation_request, build_default_engine_command, create_engine_provider, render_engine_message, write_engine_message


AIDER_MESSAGE_FILE = ENGINE_MESSAGE_FILE
AiderProvider = EngineProvider
FakeAiderProvider = FakeEngineProvider
SubprocessAiderProvider = SubprocessEngineProvider
build_default_aider_command = build_default_engine_command
create_aider_provider = create_engine_provider
render_aider_message = render_engine_message
write_aider_message = write_engine_message

__all__ = [
    "AIDER_MESSAGE_FILE",
    "AiderProvider",
    "FakeAiderProvider",
    "MutationRequest",
    "MutationResult",
    "SubprocessAiderProvider",
    "TokenUsage",
    "apply_mutation_request",
    "build_default_aider_command",
    "create_aider_provider",
    "render_aider_message",
    "write_aider_message",
]
