import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.memunote import server, slides_api
from tests.round32_slides_test import _ServerCase

class StreamRegressions(_ServerCase):
    def test_reused_request_id_cannot_replace_the_running_chat_or_slide_stream(self):
        original = {'cancel':threading.Event(), 'conn':None}
        self.app.streams['duplicate'] = original
        built = {'memory_write':False, 'memory_facts':0, 'messages':[], 'excerpt':'', 'title':''}
        with patch('app.memunote.ai.discover', return_value='http://127.0.0.1:8317'), patch('app.memunote.prompts.build_messages', return_value=built), patch('app.memunote.ai.stream_chat') as transport:
            for path, body in [('/api/ai_chat', {'model':'fake','id':'duplicate'}), ('/api/slides_ai', {'model':'fake','id':'duplicate','task':'generate','input':{'topic':'test'}})]:
                with self.subTest(path=path):
                    response, data = self.call('POST', path, body)
                    self.assertEqual(response.status, 409, data)
                    self.assertIs(self.app.streams['duplicate'], original)
            transport.assert_not_called()
        self.assertFalse(original['cancel'].is_set())

    def test_stop_normalizes_ids_like_the_start_route(self):
        request_id = 'r' * 90
        state = {'cancel':threading.Event(), 'conn':None}
        self.app.streams[request_id[:64]] = state
        response, _ = self.call('POST', '/api/ai_stop', {'id':request_id})
        self.assertEqual(response.status, 200)
        self.assertTrue(state['cancel'].is_set())

    def test_a_disconnect_while_sending_headers_releases_the_request(self):
        built = {'memory_write':False, 'memory_facts':0, 'messages':[], 'excerpt':'', 'title':''}
        def disconnected():raise BrokenPipeError('test client disconnected')
        body = {'model':'fake','id':'header-test','task':'generate','input':{'topic':'test'}}
        handler = SimpleNamespace(app=self.app, _json_body=lambda:body, send_response=lambda *_:None,
                                  send_header=lambda *_:None, end_headers=disconnected)
        with patch('app.memunote.ai.discover', return_value='http://127.0.0.1:8317'), patch('app.memunote.prompts.build_messages', return_value=built):
            for action in [server.Handler.api_ai_chat, slides_api.api_slides_ai]:
                action(handler, 'POST')
                self.assertNotIn('header-test', self.app.streams)

if __name__ == '__main__':
    unittest.main()
