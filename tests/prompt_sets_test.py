"""Prompt sets: the original library stays read-only, copies are editable."""
import unittest

from app import pgdata


class PromptSetsTest(unittest.TestCase):
    def test_builtin_set_is_locked_and_complete(self):
        builtin = pgdata.builtin_prompt_set()
        self.assertTrue(builtin["builtin"])
        self.assertEqual(builtin["id"], pgdata.BUILTIN_PROMPT_SET_ID)
        self.assertEqual(len(builtin["internal_prompts"]), len(pgdata.INTERNAL_PROMPTS))

    def test_default_document_activates_the_original(self):
        doc = pgdata.default_prompts()
        self.assertEqual(doc["schema_version"], pgdata.PROMPT_SCHEMA_VERSION)
        self.assertEqual(doc["active_set_id"], pgdata.BUILTIN_PROMPT_SET_ID)
        self.assertEqual(doc["default_id"], "memuai-default")
        self.assertTrue(doc["prompts"])

    def test_builtin_set_cannot_be_edited(self):
        tampered = pgdata.builtin_prompt_set()
        tampered["prompts"][0]["text"] = "hacked"
        tampered["internal_prompts"][0]["text"] = "hacked"
        doc = pgdata._prompt_document([tampered], pgdata.BUILTIN_PROMPT_SET_ID)
        restored = doc["prompt_sets"][0]
        self.assertEqual(restored["prompts"][0]["text"], pgdata.DEFAULT_PROMPT)
        self.assertNotEqual(restored["internal_prompts"][0]["text"], "hacked")

    def test_user_set_keeps_its_own_prompts(self):
        internal_id = pgdata.INTERNAL_PROMPTS[0]["id"]
        custom = {
            "id": "mine",
            "name": "Mine",
            "prompts": [{"id": "memuai-default", "name": "Mine", "text": "my system prompt"}],
            "internal_prompts": [{"id": internal_id, "text": "my internal prompt"}],
            "default_id": "memuai-default",
            "disabled_for": [],
        }
        doc = pgdata._prompt_document([custom], "mine")
        self.assertEqual(doc["active_set_id"], "mine")
        self.assertEqual(doc["prompts"][0]["text"], "my system prompt")
        mine = next(x for x in doc["prompt_sets"] if x["id"] == "mine")
        self.assertFalse(mine["builtin"])
        self.assertEqual(len(mine["internal_prompts"]), len(pgdata.INTERNAL_PROMPTS))
        edited = next(x for x in doc["internal_prompts"] if x["id"] == internal_id)
        self.assertEqual(edited["text"], "my internal prompt")
        # the original set is still there, untouched
        original = next(x for x in doc["prompt_sets"] if x["id"] == pgdata.BUILTIN_PROMPT_SET_ID)
        self.assertEqual(original["prompts"][0]["text"], pgdata.DEFAULT_PROMPT)

    def test_unknown_active_set_falls_back_to_original(self):
        doc = pgdata._prompt_document([], "ghost")
        self.assertEqual(doc["active_set_id"], pgdata.BUILTIN_PROMPT_SET_ID)


if __name__ == "__main__":
    unittest.main()
