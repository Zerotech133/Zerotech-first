import importlib
import unittest


profiles = importlib.import_module("AI.111111.chat_profiles")


class ChatProfileTests(unittest.TestCase):
    def test_builtin_profiles_expose_role_prompt_and_model(self):
        catalog = profiles.list_profiles()
        self.assertIn("general", catalog)
        self.assertIn("research", catalog)
        self.assertIn("risk_review", catalog)
        research = profiles.get_profile("research")
        self.assertTrue(research["system_prompt"])
        self.assertEqual(research["model"], "qwen3.5:9b")
        self.assertTrue(research["rag"])

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(profiles.ProfileError):
            profiles.get_profile("missing")

    def test_profile_specs_include_display_name_and_model(self):
        specs = profiles.list_profile_specs()
        general = next(item for item in specs if item["id"] == "general")
        self.assertEqual(general["name"], "通用助手")
        self.assertEqual(general["model"], "qwen3.5:9b")


if __name__ == "__main__":
    unittest.main()
