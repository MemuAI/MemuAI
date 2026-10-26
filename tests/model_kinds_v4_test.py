import unittest
from unittest.mock import patch
from app import model_catalog as catalog, model_capabilities as capabilities

class ModelKinds(unittest.TestCase):
    def test_provider_metadata_preserves_specialized_tasks(self):
        for declared,expected in [('reranker','rerank'),('transcription','stt'),('text-to-speech','tts'),('decisions','decisions'),('embedding','embedding')]:
            with self.subTest(declared=declared):
                self.assertEqual(capabilities.extract_provider({'model_type':declared})['kind'],expected)
                self.assertEqual(capabilities.extract_provider({'kind':declared})['kind'],expected)
        self.assertEqual(capabilities.extract_provider({'supported_endpoint_types':['rerank']})['kind'],'rerank')
        self.assertNotIn('kind',capabilities.extract_provider({'supported_endpoint_types':['chat','rerank']}))

    def test_catalog_types_and_text_filter(self):
        kinds=['chat','image','video','music','audio','stt','tts','embedding','rerank','decisions']
        payload={'models':[{'id':'demo-'+k,'provider':'custom','caps':{'kind':k}} for k in kinds]}
        with patch.object(catalog,'vision_state',return_value={'state':'unknown','src':'none'}):
            catalog.enrich(payload)
        self.assertEqual([r['kind'] for r in payload['models']],kinds)
        self.assertEqual([r['kind'] for r in catalog.pick_rows(payload['models'],kind='text')],['chat'])
        for kind in kinds:
            self.assertEqual([r['kind'] for r in catalog.pick_rows(payload['models'],kind=kind)],[kind])

    def test_name_hints_do_not_mark_reasoning_or_audio_chat_nontext(self):
        self.assertEqual(capabilities.heuristics('bge-reranker-v2-m3')['kind'],'rerank')
        self.assertEqual(capabilities.heuristics('text-embedding-3-small')['kind'],'embedding')
        self.assertEqual(capabilities.heuristics('whisper-large-v3')['kind'],'stt')
        self.assertEqual(capabilities.heuristics('gpt-4o-audio-preview')['kind'],'chat')
        self.assertEqual(capabilities.heuristics('deepseek-r1')['kind'],'chat')
        self.assertEqual(capabilities.heuristics('decision-v2')['kind'],'decisions')

if __name__=='__main__':unittest.main()
