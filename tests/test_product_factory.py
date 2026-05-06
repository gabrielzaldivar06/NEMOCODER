import unittest

from nemo_coding_platform.core.product_factory import get_platform_info


class ProductFactoryTests(unittest.TestCase):
    def test_discovers_aider_platform_info(self) -> None:
        info = get_platform_info("aider")

        self.assertEqual(info["product_base"], "aider")
        self.assertTrue(info["capabilities"]["full_handoff"])
        self.assertIn("prime_context", info["nemo_tools_by_phase"]["plan"])

    def test_rejects_unknown_product(self) -> None:
        with self.assertRaises(ValueError):
            get_platform_info("unknown")


if __name__ == "__main__":
    unittest.main()
