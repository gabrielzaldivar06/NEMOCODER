import sys
import tempfile
import unittest

from nemo_coding_platform.core.engine_interface import FakeEngineProvider, MutationRequest, SubprocessEngineProvider, apply_mutation_request, build_default_engine_command, create_engine_provider, render_engine_message
from nemo_coding_platform.core.model_config import DEFAULT_LMSTUDIO_MODEL
from nemo_coding_platform.core.model_config import ModelProfile
from nemo_coding_platform.core.mutations import QualityMutationEngine
from nemo_coding_platform.core.workspace import Workspace


class AiderInterfaceTests(unittest.TestCase):
    def test_default_engine_command_targets_lmstudio_model(self) -> None:
        profile = ModelProfile(model="nvidia.agentic.coder-4b", base_url="http://localhost:1234/v1")
        command = build_default_engine_command(profile, ".nemo-engine-message.md")

        self.assertIn("-m", command)
        self.assertIn("nemo_code_runtime", command)
        self.assertIn("openai/nvidia.agentic.coder-4b", command)
        self.assertIn("http://localhost:1234/v1", command)
        self.assertIn("--message-file", command)

    def test_render_engine_message_includes_context_and_acceptance(self) -> None:
        message = render_engine_message(MutationRequest("Build", "spec.md", ("passes tests",), "NEMO context"))

        self.assertIn("Build", message)
        self.assertIn("passes tests", message)
        self.assertIn("NEMO context", message)

    def test_create_engine_provider_returns_typed_provider_for_mode(self) -> None:
        self.assertIsInstance(create_engine_provider("fake"), FakeEngineProvider)
        self.assertIsInstance(create_engine_provider("subprocess"), SubprocessEngineProvider)

    def test_create_engine_provider_rejects_unknown_mode(self) -> None:
        with self.assertRaises(ValueError):
            create_engine_provider("other")

    def test_fake_provider_applies_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = QualityMutationEngine(Workspace.from_path(tmp))
            result = apply_mutation_request(
                engine,
                FakeEngineProvider(),
                MutationRequest("Build feature", "generated-spec.md", ("passes",), "context"),
            )

            self.assertEqual(result.provider, "fake-nemo-code")
            self.assertEqual(result.applied_files, ("generated-implementation.md",))
            self.assertEqual(result.changed_files, ("generated-implementation.md",))
            self.assertEqual(result.model_profile.model, DEFAULT_LMSTUDIO_MODEL)
            self.assertIn("after/generated-implementation.md", result.diff_artifact)
            self.assertTrue(Workspace.from_path(tmp).resolve_inside("generated-implementation.md").exists())

    def test_subprocess_provider_detects_direct_runtime_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = QualityMutationEngine(Workspace.from_path(tmp))
            provider = SubprocessEngineProvider((sys.executable, "-c", "from pathlib import Path; Path('subprocess-created.txt').write_text('ok', encoding='utf-8'); print('created')"))
            result = apply_mutation_request(
                engine,
                provider,
                MutationRequest("Build feature", "generated-spec.md", ("passes",), "context", provider_mode="subprocess", runtime_path=tmp),
            )

            self.assertEqual(result.provider, "subprocess-nemo-code")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.applied_files, ())
            self.assertEqual(result.changed_files, ("subprocess-created.txt",))
            self.assertIn("created", result.stdout)
            self.assertIn(".nemo-engine-message.md", provider.last_message_file)

    def test_subprocess_provider_noop_does_not_fake_changed_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = QualityMutationEngine(Workspace.from_path(tmp))
            provider = SubprocessEngineProvider((sys.executable, "-c", "print('noop')"))
            result = apply_mutation_request(
                engine,
                provider,
                MutationRequest("Build feature", "generated-spec.md", ("passes",), "context", provider_mode="subprocess", runtime_path=tmp),
            )

            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.changed_files, ())
            self.assertEqual(result.applied_files, ())

    def test_subprocess_provider_timeout_returns_auditable_noop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = QualityMutationEngine(Workspace.from_path(tmp))
            provider = SubprocessEngineProvider((sys.executable, "-c", "import time; time.sleep(10)"))
            result = apply_mutation_request(
                engine,
                provider,
                MutationRequest("Build feature", "generated-spec.md", ("passes",), "context", provider_mode="subprocess", runtime_path=tmp, timeout_seconds=0.01),
            )

            self.assertIsNone(result.returncode)
            self.assertEqual(result.changed_files, ())
            self.assertIn("timed out", result.stderr)

    def test_subprocess_provider_extracts_token_usage_from_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = QualityMutationEngine(Workspace.from_path(tmp))
            provider = SubprocessEngineProvider(
                (
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('.nemo-token-usage.json').write_text('{\"usage\":{\"prompt_tokens\":21,\"completion_tokens\":9,\"total_tokens\":30},\"model\":\"openai/test\",\"source\":\"real\"}', encoding='utf-8')",
                )
            )
            result = apply_mutation_request(
                engine,
                provider,
                MutationRequest("Build feature", "generated-spec.md", ("passes",), "context", provider_mode="subprocess", runtime_path=tmp),
            )

            self.assertIsNotNone(result.token_usage)
            self.assertEqual(result.token_usage.total_tokens, 30)
            self.assertEqual(result.token_usage.prompt_tokens, 21)
            self.assertEqual(result.token_usage.completion_tokens, 9)

    def test_subprocess_provider_extracts_token_usage_from_stdout_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = QualityMutationEngine(Workspace.from_path(tmp))
            provider = SubprocessEngineProvider(
                (
                    sys.executable,
                    "-c",
                    "print('NEMO_TOKEN_USAGE_JSON={\"usage\":{\"prompt_tokens\":13,\"completion_tokens\":7,\"total_tokens\":20},\"model\":\"openai/test2\"}')",
                )
            )
            result = apply_mutation_request(
                engine,
                provider,
                MutationRequest("Build feature", "generated-spec.md", ("passes",), "context", provider_mode="subprocess", runtime_path=tmp),
            )

            self.assertIsNotNone(result.token_usage)
            self.assertEqual(result.token_usage.total_tokens, 20)
            self.assertEqual(result.token_usage.model_name, "openai/test2")


if __name__ == "__main__":
    unittest.main()
