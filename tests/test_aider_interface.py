import sys
import tempfile
import unittest

from nemo_coding_platform.core.aider_interface import FakeAiderProvider, MutationRequest, SubprocessAiderProvider, apply_mutation_request, build_default_aider_command, create_aider_provider, render_aider_message
from nemo_coding_platform.core.model_config import DEFAULT_LMSTUDIO_MODEL
from nemo_coding_platform.core.model_config import ModelProfile
from nemo_coding_platform.core.mutations import QualityMutationEngine
from nemo_coding_platform.core.workspace import Workspace


class AiderInterfaceTests(unittest.TestCase):
    def test_default_aider_command_targets_lmstudio_model(self) -> None:
        profile = ModelProfile(model="nvidia.agentic.coder-4b", base_url="http://localhost:1234/v1")
        command = build_default_aider_command(profile, ".nemo-aider-message.md")

        self.assertIn("-m", command)
        self.assertIn("aider", command)
        self.assertIn("openai/nvidia.agentic.coder-4b", command)
        self.assertIn("http://localhost:1234/v1", command)
        self.assertIn("--message-file", command)

    def test_render_aider_message_includes_context_and_acceptance(self) -> None:
        message = render_aider_message(MutationRequest("Build", "spec.md", ("passes tests",), "NEMO context"))

        self.assertIn("Build", message)
        self.assertIn("passes tests", message)
        self.assertIn("NEMO context", message)

    def test_create_aider_provider_returns_typed_provider_for_mode(self) -> None:
        self.assertIsInstance(create_aider_provider("fake"), FakeAiderProvider)
        self.assertIsInstance(create_aider_provider("subprocess"), SubprocessAiderProvider)

    def test_create_aider_provider_rejects_unknown_mode(self) -> None:
        with self.assertRaises(ValueError):
            create_aider_provider("other")

    def test_fake_provider_applies_inside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = QualityMutationEngine(Workspace.from_path(tmp))
            result = apply_mutation_request(
                engine,
                FakeAiderProvider(),
                MutationRequest("Build feature", "generated-spec.md", ("passes",), "context"),
            )

            self.assertEqual(result.provider, "fake-aider")
            self.assertEqual(result.applied_files, ("generated-implementation.md",))
            self.assertEqual(result.changed_files, ("generated-implementation.md",))
            self.assertEqual(result.model_profile.model, DEFAULT_LMSTUDIO_MODEL)
            self.assertIn("after/generated-implementation.md", result.diff_artifact)
            self.assertTrue(Workspace.from_path(tmp).resolve_inside("generated-implementation.md").exists())

    def test_subprocess_provider_detects_direct_runtime_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = QualityMutationEngine(Workspace.from_path(tmp))
            provider = SubprocessAiderProvider((sys.executable, "-c", "from pathlib import Path; Path('subprocess-created.txt').write_text('ok', encoding='utf-8'); print('created')"))
            result = apply_mutation_request(
                engine,
                provider,
                MutationRequest("Build feature", "generated-spec.md", ("passes",), "context", provider_mode="subprocess", runtime_path=tmp),
            )

            self.assertEqual(result.provider, "subprocess-aider")
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.applied_files, ())
            self.assertEqual(result.changed_files, ("subprocess-created.txt",))
            self.assertIn("created", result.stdout)
            self.assertIn(".nemo-aider-message.md", provider.last_message_file)

    def test_subprocess_provider_noop_does_not_fake_changed_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            engine = QualityMutationEngine(Workspace.from_path(tmp))
            provider = SubprocessAiderProvider((sys.executable, "-c", "print('noop')"))
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
            provider = SubprocessAiderProvider((sys.executable, "-c", "import time; time.sleep(10)"))
            result = apply_mutation_request(
                engine,
                provider,
                MutationRequest("Build feature", "generated-spec.md", ("passes",), "context", provider_mode="subprocess", runtime_path=tmp, timeout_seconds=0.01),
            )

            self.assertIsNone(result.returncode)
            self.assertEqual(result.changed_files, ())
            self.assertIn("timed out", result.stderr)


if __name__ == "__main__":
    unittest.main()
