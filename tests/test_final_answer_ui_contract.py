import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestFinalAnswerUiContract(unittest.TestCase):
    def test_server_forwards_final_answer_event(self):
        src = (ROOT / "server_final.py").read_text(encoding="utf-8")
        self.assertIn('elif event_type == "final_answer"', src)
        self.assertIn("final_answer_payload", src)
        self.assertIn("done_payload['final_answer']", src)

    def test_frontend_handles_final_answer_and_structured_frame(self):
        src = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("data.type === 'final_answer'", src)
        self.assertIn("renderAssistantStructuredFrame", src)
        self.assertIn("thinkingTrace = fullResponse.substring", src)
        self.assertIn("buildAssistantStructuredHtml", src)

    def test_frontend_startup_restore_merges_backend_history(self):
        src = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("async function loadMostRecentChat()", src)
        self.assertIn("const backendHistory = await fetchSessionHistory(currentChatId)", src)
        self.assertIn("mergeHistoryPreferLocal(messageHistory, backendHistory)", src)
        self.assertIn("localStorage.setItem(`chat_${currentChatId}`", src)

    def test_deep_clean_persists_metadata_only_merges(self):
        src = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        self.assertIn("const beforeMergeSerialized = JSON.stringify(cleaned)", src)
        self.assertIn("const afterMergeSerialized = JSON.stringify(cleaned)", src)
        self.assertIn("afterMergeSerialized !== beforeMergeSerialized", src)
        self.assertIn("merged richer backend metadata into local history", src)

    def test_frontend_styles_exist_for_thinking_and_final_panels(self):
        src = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn(".assistant-thinking-block", src)
        self.assertIn(".assistant-final-panel", src)
        self.assertIn(".assistant-final-summary", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
