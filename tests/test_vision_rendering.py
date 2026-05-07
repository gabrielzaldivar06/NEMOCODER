import unittest
from nemo_coding_platform.core.aider_interface import MutationRequest, render_aider_message, ModelProfile


class TestVisionRendering(unittest.TestCase):
    def test_render_with_image(self):
        request = MutationRequest(
            objective="Build UI",
            spec_path="spec.md",
            acceptance_criteria=("matches design",),
            context="Existing code",
            image_path="docs/mockup.png",
            skill_prompt="Use Tailwind"
        )
        
        message = render_aider_message(request)
        self.assertIn("# Design Reference", message)
        self.assertIn("docs/mockup.png", message)
        self.assertIn("Use your vision capabilities", message)
        self.assertIn("# Skill Guidance", message)
        self.assertIn("Use Tailwind", message)

    def test_render_without_image(self):
        request = MutationRequest(
            objective="Build UI",
            spec_path="spec.md",
            acceptance_criteria=("matches design",),
            context="Existing code"
        )
        
        message = render_aider_message(request)
        self.assertNotIn("# Design Reference", message)


if __name__ == "__main__":
    unittest.main()
