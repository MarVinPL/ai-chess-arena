import json
import threading
import unittest
from unittest.mock import patch
import passkey_test

class FakeResponse:
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self): return json.dumps({'choices':[{'message':{'content':'Passkey używa pary kluczy publicznego i prywatnego.'}}]}).encode()

class FakeRuntime:
    def __init__(self): self.loaded=[]
    def ensure_loaded(self, path): self.loaded.append(str(path))

class PasskeyTestTests(unittest.TestCase):
    def test_exactly_three_fixed_questions(self):
        self.assertEqual(len(passkey_test.QUESTIONS), 3)
        self.assertIn('RP ID', passkey_test.QUESTIONS[1])
        self.assertIn('Telegram', passkey_test.QUESTIONS[2])

    def test_first_time_competitor_gets_all_three_knowledge_questions(self):
        self.assertEqual([n for n,_ in passkey_test.questions_for_tournament([])], [1,2,3])

    def test_known_answers_are_reused_and_only_one_random_check_is_scheduled(self):
        class Chooser:
            def choice(self, values): return values[1]
        scheduled=passkey_test.questions_for_tournament([1,2,3],False,Chooser())
        self.assertEqual(len(scheduled),1)
        self.assertEqual(scheduled[0][0],0)
        self.assertEqual(scheduled[0][1],passkey_test.QUESTIONS[1])
        self.assertEqual(passkey_test.questions_for_tournament([1,2,3],True,Chooser()),[])

    def test_only_missing_knowledge_questions_are_repeated(self):
        scheduled=passkey_test.questions_for_tournament([1,3])
        self.assertEqual(scheduled,[(2,passkey_test.QUESTIONS[1])])

    def test_answer_uses_no_response_timeout_and_measures_time(self):
        runtime=FakeRuntime()
        with patch.object(passkey_test.urllib.request, 'urlopen', return_value=FakeResponse()) as opened:
            result=passkey_test.ask_passkey_question(runtime, '/models/a.gguf', passkey_test.QUESTIONS[0])
        self.assertEqual(result['status'], 'answered')
        self.assertIn('kluczy', result['answer'])
        self.assertGreaterEqual(result['seconds'], 0)
        self.assertIsNone(opened.call_args.kwargs['timeout'])
        self.assertEqual(runtime.loaded, ['/models/a.gguf'])

    def test_stop_does_not_submit_question(self):
        event=threading.Event(); event.set(); runtime=FakeRuntime()
        with patch.object(passkey_test.urllib.request, 'urlopen') as opened:
            result=passkey_test.ask_passkey_question(runtime, '/models/a.gguf', passkey_test.QUESTIONS[0], event)
        self.assertEqual(result['status'], 'stopped')
        opened.assert_not_called()

if __name__ == '__main__': unittest.main()
