"""Round 45: command arguments, general assistant tools, speech and autosave backend."""
from __future__ import annotations

import asyncio
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app import telegram_use as use
from app import telegram_use_ai as ai
from app import telegram_use_arguments as arguments
from app import telegram_use_config as config
from app import telegram_use_voice as voice
from tests.round43_telegram_use_test import Client, message


class Arguments(unittest.TestCase):
    def summary(self):
        return config.command(config.DEFAULT_COMMANDS[2])

    def test_summary_required_count_and_optional_multiword_chat(self):
        command = self.summary()
        self.assertEqual(command["kind"], "summary")
        self.assertEqual(arguments.parse(command, "50 AI Telegram News")["chat"], "AI Telegram News")
        self.assertEqual(arguments.parse(command, '50 "AI Telegram News"')["chat"], "AI Telegram News")
        self.assertEqual(arguments.parse(command, "50")["chat"], "")
        for text in ("", "many", "0", "-10", "1001", "50.5"):
            with self.subTest(text=text), self.assertRaises(arguments.ArgumentError): arguments.parse(command, text)

    def test_legacy_summary_upgrades_without_changing_custom_prompt(self):
        command = config.command({"name":"summary", "trigger":"!//summary", "kind":"question", "prompt":"My custom instructions {question}"})
        self.assertEqual(command["kind"], "summary")
        self.assertEqual(command["prompt"], "My custom instructions {question}")
        self.assertEqual(len(command["arguments"]), 2)

    def test_custom_types_defaults_and_safe_placeholder_substitution(self):
        command = config.command({"trigger":"!//translate", "arguments":[
            {"name":"language", "type":"choice", "choices":["Russian","English"], "required":True},
            {"name":"formal", "type":"boolean", "default":"yes"},
            {"name":"text", "type":"text", "default":""}]})
        parsed = arguments.parse(command, 'english no Hello O\'Neill {language}')
        self.assertEqual(parsed["language"], "English")
        self.assertFalse(parsed["formal"])
        self.assertEqual(arguments.parse(command,"Russian")["formal"],True)
        self.assertEqual(arguments.render("Translate {text} into {language}", parsed), "Translate Hello O'Neill {language} into English")

    def test_schema_rejects_duplicates_reserved_names_nonfinite_bounds_and_invalid_default(self):
        for specs in ([{"name":"question"}], [{"name":"x"},{"name":"x"}],
                      [{"name":"a", "type":"integer", "min":"nan"}],
                      [{"name":"a", "type":"integer", "default":"word"}],
                      [{"name":"a", "type":"choice", "choices":[]}],
                      [{"name":"bad name"}], [{"name":"x", "min":10,"max":1}]):
            with self.subTest(specs=specs), self.assertRaises(ValueError): arguments.schema(specs)

    def test_numeric_bounds_and_extra_arguments_fail(self):
        command = config.command({"trigger":"!//test", "arguments":[{"name":"n","type":"number","min":1,"max":3,"required":True}]})
        self.assertEqual(arguments.parse(command, "2.5")["n"], 2.5)
        for text in ("nan", "inf", "0", "4", "2 extra"):
            with self.subTest(text=text), self.assertRaises(arguments.ArgumentError): arguments.parse(command,text)


class Runtime(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="memu-tg45-")
        self.pathpatch = patch.object(config,"path",return_value=Path(self.tmp.name)/"telegram_use.json")
        self.pathpatch.start()
        config.save({"enabled":True,"api_id":123,"commands_enabled":True,"model":"fake"})
        config.secrets().set("api_hash","b"*32)
        self.client=Client(); self.runtime=use.Runtime(lambda:self.client)
        await self.runtime._connect()
    async def asyncTearDown(self):
        await self.runtime._action("close",{})
        self.pathpatch.stop(); config._stores.clear(); self.tmp.cleanup()
    def event(self,text="!//summary 2",own=True):
        msg=message(10,text,sender=42 if own else 9,outgoing=own)
        self.client.rows.append(msg)
        return types.SimpleNamespace(message=msg,sender_id=msg.sender_id,chat_id=7,is_private=True)
    async def settle(self):
        if self.runtime._tasks: await asyncio.gather(*self.runtime._tasks)
    async def test_summary_reads_count_chronologically_and_delivers_in_invoking_chat(self):
        with patch.object(ai,"complete",AsyncMock(return_value="summary")) as complete:
            await self.runtime._on_event(self.event()); await self.settle()
            content=complete.call_args.args[1][1]["content"]
            self.assertLess(content.index("second"),content.index("third"))
            self.assertIn("requested 2",content)
            self.assertNotIn("first",content)
        self.assertEqual(self.client.edit_message.call_args.args[:2],(7,10))
        self.assertEqual(self.runtime.last_command["phase"],"done")
    async def test_summary_uses_fuzzy_stylized_channel_name_but_writes_to_current_chat(self):
        self.client.dialogs=[types.SimpleNamespace(id=-100,name="AI T@l@gram News",entity=types.SimpleNamespace(username="news"),input_entity="channel")]
        calls=[]
        original=self.client.iter_messages
        async def history(chat,**kw):
            calls.append((chat,kw))
            async for row in original(chat,**kw): yield row
        self.client.iter_messages=history
        with patch.object(ai,"complete",AsyncMock(return_value="summary")):
            await self.runtime._on_event(self.event("!//summary 50 AI Telegram News")); await self.settle()
        self.assertEqual(calls[0],("channel",{"limit":50,"offset_id":0}))
        self.assertEqual(self.client.edit_message.call_args.args[0],7)
    async def test_ambiguous_fuzzy_name_never_reads_arbitrary_chat(self):
        self.client.dialogs=[types.SimpleNamespace(id=i,name=name,entity=types.SimpleNamespace(username=""),input_entity=str(i))
            for i,name in enumerate(["AI T@l@gram News", "AI Telegr@m Newz"],1)]
        with self.assertRaisesRegex(use.TelegramUseError,"Ambiguous"):
            await self.runtime.resolve("AI Telegram News")
    async def test_other_allowed_user_cannot_summarize_another_chat(self):
        cfg=config.load(); cfg["commands"][2]["allowed_users"]=[9]; config.save(cfg)
        with patch.object(ai,"complete",AsyncMock(return_value="secret")) as complete:
            await self.runtime._on_event(self.event("!//summary 50 Иван",own=False)); await self.settle()
            complete.assert_not_awaited()
        self.client.send_message.assert_not_awaited()
        self.assertIn("Other users", self.runtime.error)
    async def test_missing_count_reports_usage_without_destroying_command(self):
        with patch.object(ai,"complete",AsyncMock()) as complete:
            await self.runtime._on_event(self.event("!//summary")); await self.settle()
            complete.assert_not_awaited()
        self.client.edit_message.assert_not_awaited()
        self.assertIn("!//summary <count>",self.client.send_message.call_args.args[1])
    async def test_voice_command_needs_no_default_text_model(self):
        config.save({"model":"","voice_cleanup_enabled":False})
        event=self.event("!//voice")
        audio=message(media=object()); audio.voice=object(); audio.document=types.SimpleNamespace(mime_type="audio/ogg",size=1000)
        event.message.reply_to_msg_id=1; event.message.get_reply_message=AsyncMock(return_value=audio)
        with patch("app.telegram_voice.transcribe",AsyncMock(return_value="raw transcription")) as recognize, patch.object(ai,"complete",AsyncMock()) as complete:
            await self.runtime._on_event(event); await self.settle()
            recognize.assert_awaited_once(); complete.assert_not_awaited()
            self.assertTrue(recognize.call_args.kwargs["telegram_use"])
        self.assertEqual(self.client.edit_message.call_args.args[2],"raw transcription")
    async def test_same_hash_and_proxy_autosave_does_not_disconnect_or_drop_session(self):
        config.secrets().set("proxy","socks5://localhost:1080")
        config.secrets().set("session","fake-session")
        with patch.object(self.runtime,"_disconnect",AsyncMock()) as disconnect:
            await self.runtime.action("settings",{"api_hash":"b"*32,"proxy":"socks5://localhost:1080","system_prompt":"concise"})
            disconnect.assert_not_awaited()
        self.assertTrue(config.secrets().has("session"))
    async def test_voice_preferences_are_independent_and_cleanup_does_not_run_twice(self):
        config.save({"stt_source":"local","stt_local_model":"medium","stt_language":"ru"})
        prefs=voice.preferences({"voice_stt_provider":"api","voice_stt_local_model":"small","voice_postprocess_enabled":True,"voice_language":"en"})
        self.assertEqual((prefs["voice_stt_provider"],prefs["voice_stt_local_model"],prefs["voice_language"]),("local","medium","ru"))
        self.assertFalse(prefs["voice_postprocess_enabled"])
    async def test_optional_cleanup_uses_only_cleanup_model_and_exact_default_prompt(self):
        config.save({"voice_cleanup_enabled":True,"voice_cleanup_model":"cleanup"})
        from app.pgdata import internal_prompt
        with patch("app.telegram_voice.transcribe",AsyncMock(return_value="words")), patch.object(ai,"complete",AsyncMock(return_value="Words.")) as complete:
            result=await voice.transcribe(b"x"*1000,"audio/ogg")
        self.assertEqual(result["text"],"Words."); self.assertTrue(result["postprocessed"])
        self.assertEqual(complete.call_args.args[0],"cleanup")
        self.assertIn(internal_prompt("voice-postprocess"),complete.call_args.args[1][0]["content"])
        self.assertEqual(complete.call_args.kwargs,{"allow_tools":False,"general":False})
    async def test_failed_cleanup_keeps_usable_transcript(self):
        config.save({"voice_cleanup_enabled":True,"voice_cleanup_model":"bad"})
        with patch("app.telegram_voice.transcribe",AsyncMock(return_value="keep me")), patch.object(ai,"complete",AsyncMock(side_effect=ValueError("cleanup failed"))):
            result=await voice.transcribe(b"x"*1000,"audio/ogg")
        self.assertEqual(result["text"],"keep me"); self.assertFalse(result["postprocessed"])
        self.assertIn("cleanup failed",result["postprocess_error"])
    async def test_quick_access_source_reuses_its_model_but_independent_language(self):
        config.save({"stt_source":"quick","stt_language":"auto"})
        with patch("app.assistant_commands.voice_prefs",return_value={"voice_stt_provider":"local","voice_stt_local_model":"medium","voice_postprocess_enabled":True}) as quick:
            prefs=voice.preferences({"voice_stt_local_model":"small"})
        quick.assert_called_once()
        self.assertEqual(prefs["voice_stt_local_model"],"medium")
        self.assertEqual(prefs["voice_language"],"auto")
        self.assertFalse(prefs["voice_postprocess_enabled"])
    async def test_window_stt_fallback_forwards_server_owned_settings_and_requests_raw_text(self):
        from app import telegram_voice
        with patch.object(telegram_voice,"status",return_value={"available":True,"engine":"app","model":"small"}),\
             patch.object(telegram_voice,"_window_url",return_value="http://127.0.0.1:8132"),\
             patch.object(telegram_voice,"_post_window",return_value={"text":"transcript"}) as post:
            text=await telegram_voice.transcribe(b"x"*1000,"audio/ogg",{},telegram_use=True)
        self.assertEqual(text,"transcript")
        body=post.call_args.args[1]
        self.assertIs(body["telegram_use"],True);self.assertIs(body["raw"],True)
        self.assertNotIn("api_key",body)


class AI(unittest.IsolatedAsyncioTestCase):
    def tool(self,name="web_search",args=None):
        return "```memuai-tool\n"+json.dumps([{"tool":name,"args":args or {"query":"latest"}}])+"\n```"
    async def test_search_results_reach_model_and_call_markup_is_removed(self):
        with patch.object(ai,"_completion",AsyncMock(side_effect=[self.tool(),"[Source](https://example.com) answer"])) as completion, patch.object(ai,"read_tool",AsyncMock(return_value={"results":[{"url":"https://example.com","snippet":"facts"}]})) as read:
            answer=await ai.complete("kiro-model",[{"role":"user","content":"Latest news?"}])
        self.assertNotIn("memuai-tool",answer); self.assertIn("Source",answer)
        read.assert_awaited_once_with("web_search",{"query":"latest"})
        prompt=completion.call_args_list[0].args[1][0]["content"]
        self.assertIn("general-purpose",prompt); self.assertIn("not running a coding session",prompt)
        self.assertIn("facts",completion.call_args.args[1][-1]["content"])
    async def test_no_write_tool_can_escape_readonly_allowlist(self):
        with patch.object(ai,"_completion",AsyncMock(side_effect=[self.tool("telegram_use_send",{"chat":"7","text":"bad"}),"Not available"])):
            with patch("app.telegram_use.run_tool") as write:
                answer=await ai.complete("model",[{"role":"user","content":"test"}])
                write.assert_not_called()
        self.assertEqual(answer,"Not available")
    async def test_search_tool_failure_reaches_model_as_error(self):
        with patch.object(ai,"_completion",AsyncMock(side_effect=[self.tool(),"Search is unavailable"])) as complete, patch.object(ai,"read_tool",AsyncMock(side_effect=RuntimeError("search offline"))):
            answer=await ai.complete("model",[{"role":"user","content":"test"}])
        self.assertIn("search offline",complete.call_args.args[1][-1]["content"])
        self.assertIn("unavailable",answer)
    async def test_cleanup_disables_tools_even_when_model_outputs_a_call(self):
        with patch.object(ai,"_completion",AsyncMock(return_value=self.tool())), patch.object(ai,"read_tool",AsyncMock()) as read:
            with self.assertRaisesRegex(RuntimeError,"unavailable tool"):
                await ai.complete("model",[{"role":"user","content":"test"}],allow_tools=False,general=False)
            read.assert_not_awaited()
    async def test_tool_loop_is_bounded(self):
        with patch.object(ai,"_completion",AsyncMock(return_value=self.tool())), patch.object(ai,"read_tool",AsyncMock(return_value={})) as read:
            with self.assertRaisesRegex(RuntimeError,"did not finish"):
                await ai.complete("model",[{"role":"user","content":"test"}])
        self.assertEqual(read.await_count,6)


if __name__ == "__main__": unittest.main()
