"""Output-token limit configuration: where num_predict / max_tokens come from and that it reaches the provider request."""
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from llm import client  # noqa: E402
from llm.client import OllamaLLM, OpenAILLM, agent_output_limits  # noqa: E402


class _Resp:
    def __init__(self, body): self.body = body
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def read(self): return json.dumps(self.body).encode()


def sent_body(llm, agent, reply):
    seen = {}

    def fake_urlopen(req, timeout=None, **kw):
        seen["body"] = json.loads(req.data.decode())
        return _Resp(reply)
    with mock.patch.object(client.urllib.request, "urlopen", fake_urlopen), mock.patch.dict(client.os.environ, {"OPENAI_API_KEY": "k"}):
        llm.complete(agent, "sys", "user")
    return seen["body"]


OLLAMA_REPLY = {"message": {"content": "{}"}, "done_reason": "stop"}
OPENAI_REPLY = {"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}], "usage": {}}


class Limits(unittest.TestCase):
    def test_defaults_report_gets_more_room_than_other_agents(self):
        self.assertEqual(agent_output_limits({}), {"report": 1500})

    def test_global_setting_is_respected_and_disables_the_report_default(self):
        self.assertEqual(agent_output_limits({"CMF_LLM_MAX_TOKENS": "900"}), {})

    def test_per_agent_override(self):
        self.assertEqual(agent_output_limits({"CMF_LLM_MAX_TOKENS_REPORT": "2200", "CMF_LLM_MAX_TOKENS": "700"}), {"report": 2200})
        self.assertEqual(agent_output_limits({"CMF_LLM_MAX_TOKENS_PLANNER": "400"})["planner"], 400)

    def test_ollama_request_carries_the_effective_limit(self):
        llm = OllamaLLM(max_tokens=700, agent_max_tokens={"report": 1800})
        self.assertEqual(sent_body(llm, "report", OLLAMA_REPLY)["options"]["num_predict"], 1800)
        self.assertEqual(sent_body(llm, "planner", OLLAMA_REPLY)["options"]["num_predict"], 700)
        self.assertEqual(sent_body(llm, "tool_selector", OLLAMA_REPLY)["options"]["num_predict"], 700)

    def test_environment_reaches_the_ollama_request(self):
        with mock.patch.dict(client.os.environ, {"CMF_LLM_MAX_TOKENS_REPORT": "2048"}, clear=False):
            llm = OllamaLLM()
        self.assertEqual(sent_body(llm, "report", OLLAMA_REPLY)["options"]["num_predict"], 2048)

    def test_openai_request_carries_the_effective_limit(self):
        llm = OpenAILLM(max_tokens=1500, agent_max_tokens={"report": 2500})
        self.assertEqual(sent_body(llm, "report", OPENAI_REPLY)["max_tokens"], 2500)
        self.assertEqual(sent_body(llm, "planner", OPENAI_REPLY)["max_tokens"], 1500)

    def test_truncated_output_is_still_rejected(self):
        # raising the limit must not weaken partial-response detection: finish_reason 'length' is still recorded as truncated
        llm = OllamaLLM(agent_max_tokens={"report": 1500})
        sent_body(llm, "report", {"message": {"content": "{\"a\":"}, "done_reason": "length", "eval_count": 1500})
        self.assertIn(llm.pop_usage().finish_reason, client.TRUNCATED_FINISH)   # the harness turns this into PartialResponseError (see test_p13_reliability)


if __name__ == "__main__":
    unittest.main()
