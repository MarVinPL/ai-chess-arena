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
        known=[passkey_test.question_fingerprint(q) for q in passkey_test.QUESTIONS]
        scheduled=passkey_test.questions_for_tournament(known,False,Chooser())
        self.assertEqual(len(scheduled),1)
        self.assertEqual(scheduled[0][0],0)
        self.assertEqual(scheduled[0][1],passkey_test.QUESTIONS[1])
        self.assertEqual(passkey_test.questions_for_tournament(known,True,Chooser()),[])

    def test_only_missing_knowledge_questions_are_repeated(self):
        known=[passkey_test.question_fingerprint(passkey_test.QUESTIONS[i]) for i in (0,2)]
        scheduled=passkey_test.questions_for_tournament(known)
        self.assertEqual(scheduled,[(2,passkey_test.QUESTIONS[1])])

    def test_modified_question_is_asked_again_even_if_old_question_number_was_answered(self):
        old=passkey_test.question_fingerprint(passkey_test.QUESTIONS[0])
        changed=passkey_test.QUESTIONS[0]+' Include attestation and user verification.'
        scheduled=passkey_test.questions_for_tournament([old])
        self.assertEqual([number for number,_ in scheduled],[2,3])
        current=[passkey_test.question_fingerprint(q) for q in passkey_test.QUESTIONS[1:]]
        revised_questions=(changed,)+passkey_test.QUESTIONS[1:]
        with patch.object(passkey_test,'QUESTIONS',revised_questions):
            scheduled=passkey_test.questions_for_tournament(current)
        self.assertEqual(scheduled,[(1,changed)])

    def test_question_fingerprint_is_independent_of_order_and_position(self):
        fingerprints={passkey_test.question_fingerprint(q) for q in passkey_test.QUESTIONS}
        reordered=tuple(reversed(passkey_test.QUESTIONS))
        with patch.object(passkey_test,'QUESTIONS',reordered):
            self.assertEqual(passkey_test.questions_for_tournament(fingerprints,True),[])

    def test_answer_uses_no_response_timeout_and_measures_time(self):
        runtime=FakeRuntime()
        with patch.object(passkey_test.urllib.request, 'urlopen', return_value=FakeResponse()) as opened:
            result=passkey_test.ask_passkey_question(runtime, '/models/a.gguf', passkey_test.QUESTIONS[0])
        self.assertEqual(result['status'], 'answered')
        self.assertIn('kluczy', result['answer'])
        self.assertGreaterEqual(result['seconds'], 0)
        self.assertIsNone(opened.call_args.kwargs['timeout'])
        self.assertEqual(runtime.loaded, ['/models/a.gguf'])

    def test_thinking_mode_and_token_budget_are_used_for_passkey_answers(self):
        runtime=FakeRuntime()
        captured={}
        def fake_open(request, timeout=None):
            captured['payload']=json.loads(request.data.decode())
            return FakeResponse()
        with patch.object(passkey_test.urllib.request,'urlopen',side_effect=fake_open):
            result=passkey_test.ask_passkey_question(runtime,'/tmp/model.gguf','Pytanie',
                enable_thinking=True,temperature=0.25,max_tokens=1800)
        self.assertEqual(result['status'],'answered')
        self.assertTrue(captured['payload']['chat_template_kwargs']['enable_thinking'])
        self.assertEqual(captured['payload']['temperature'],0.25)
        self.assertEqual(captured['payload']['max_tokens'],1800)

    def test_default_mode_keeps_thinking_disabled(self):
        runtime=FakeRuntime()
        captured={}
        def fake_open(request, timeout=None):
            captured['payload']=json.loads(request.data.decode())
            return FakeResponse()
        with patch.object(passkey_test.urllib.request,'urlopen',side_effect=fake_open):
            passkey_test.ask_passkey_question(runtime,'/tmp/model.gguf','Pytanie')
        self.assertFalse(captured['payload']['chat_template_kwargs']['enable_thinking'])
        self.assertEqual(captured['payload']['max_tokens'],900)

    def test_stop_does_not_submit_question(self):
        event=threading.Event(); event.set(); runtime=FakeRuntime()
        with patch.object(passkey_test.urllib.request, 'urlopen') as opened:
            result=passkey_test.ask_passkey_question(runtime, '/models/a.gguf', passkey_test.QUESTIONS[0], event)
        self.assertEqual(result['status'], 'stopped')
        opened.assert_not_called()

if __name__ == '__main__': unittest.main()