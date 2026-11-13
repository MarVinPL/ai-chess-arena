"""Fixed Passkey knowledge test for every model entered in a tournament."""
from __future__ import annotations
import json, random, time, urllib.request

LLM = "http://127.0.0.1:8088/v1/chat/completions"
QUESTIONS = (
    "Jak działa Passkey? Wyjaśnij kryptografię klucza publicznego i prywatnego, rolę serwera oraz odporność na phishing.",
    "Użytkownik rejestruje Passkey na Androidzie w Chrome i w Telegramie. Wyjaśnij znaczenie RP ID, origin, WebAuthn i możliwe problemy przeglądarki wbudowanej.",
    "Zaprojektuj bezpieczne zaproszenie współadministratora do Browser VPS z weryfikacją właściciela przez Telegram i rejestracją własnego Passkey. Uwzględnij jednorazowość, wygaśnięcie i odrzucenie zaproszenia.",
)


def questions_for_tournament(known_question_numbers, random_check_done=False, chooser=None):
    """Return unanswered knowledge questions, or one random health check once all are known."""
    known=set(known_question_numbers)
    missing=[(number,question) for number,question in enumerate(QUESTIONS,1) if number not in known]
    if missing:
        return missing
    if random_check_done:
        return []
    chooser=chooser or random.SystemRandom()
    return [(0,chooser.choice(QUESTIONS))]


def ask_passkey_question(runtime, model_path, question, stop_event=None, draft_model_path=None):
    if draft_model_path:
        runtime.ensure_loaded(model_path, draft_model_path)
    else:
        runtime.ensure_loaded(model_path)
    if stop_event and stop_event.is_set():
        return {"status": "stopped", "answer": "", "seconds": None}
    payload=json.dumps({"messages":[{"role":"user","content":question}],"temperature":0,"max_tokens":900,"stream":False,"chat_template_kwargs":{"enable_thinking":False}}).encode()
    req=urllib.request.Request(LLM,data=payload,headers={"Content-Type":"application/json"})
    started=time.monotonic()
    # Intentionally no timeout: measure response duration, never fail on elapsed time.
    with urllib.request.urlopen(req,timeout=None) as response:
        data=json.loads(response.read().decode())
    elapsed=time.monotonic()-started
    answer=data["choices"][0]["message"].get("content","")
    return {"status":"answered","answer":answer,"seconds":round(elapsed,4)}
