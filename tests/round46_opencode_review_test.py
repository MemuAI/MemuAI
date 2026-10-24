"""Independent OpenCode review regression: Gemini parallel call IDs without signatures."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.providers import http
from app.providers.opencode import OpenCodeProvider
from tests.round46_opencode_test import account, seed, Response, sse


class ParallelToolIdentity(unittest.IsolatedAsyncioTestCase):
    async def test_unsigned_parallel_gemini_call_keeps_native_ids_on_next_tool_turn(self):
        provider=OpenCodeProvider();acc=seed(account("zen"),dialect="gemini")
        initial=Response(lines=sse({"candidates":[{"content":{"parts":[
            {"functionCall":{"id":"native-first","name":"weather","args":{"city":"Paris"}},"thoughtSignature":"signature-first"},
            {"functionCall":{"id":"native-second","name":"weather","args":{"city":"London"}}}]},"finishReason":"STOP"}]}))
        final=Response(lines=sse({"candidates":[{"content":{"parts":[{"text":"done"}]},"finishReason":"STOP"}]}))
        session=SimpleNamespace(post=AsyncMock(side_effect=[initial,final]))
        messages=[{"role":"user","content":"Compare Paris and London weather"}]
        with patch.object(http,"get_session",AsyncMock(return_value=session)):
            events=[event async for event in provider.chat_stream(acc,"m",messages,{})]
            calls=[event for event in events if event["type"]=="tool_call"]
            messages.append({"role":"assistant","content":None,"tool_calls":[
                {"id":call["id"],"type":"function","function":{"name":call["name"],"arguments":call["arguments"]}}
                for call in calls]})
            messages.extend({"role":"tool","tool_call_id":call["id"],"content":json.dumps({"temperature":12})} for call in calls)
            _=[event async for event in provider.chat_stream(acc,"m",messages,{})]
        contents=session.post.call_args.kwargs["json"]["contents"]
        functions=[part["functionCall"] for turn in contents for part in turn["parts"] if "functionCall" in part]
        responses=[part["functionResponse"] for turn in contents for part in turn["parts"] if "functionResponse" in part]
        self.assertEqual([part.get("id") for part in functions],["native-first","native-second"])
        self.assertEqual([part.get("id") for part in responses],["native-first","native-second"])
        self.assertEqual(contents[1]["parts"][0]["thoughtSignature"],"signature-first")
        self.assertNotIn("thoughtSignature",contents[1]["parts"][1])
        self.assertTrue(initial.closed and final.closed)


if __name__=="__main__":unittest.main()
