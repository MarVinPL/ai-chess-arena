# Passkey prompt A/B comparison — 2026-11-20

## Scope and method

- Tested the exact `PASSKEY_SYSTEM_PROMPT` already committed to this branch, not an alternative wording.
- Three existing local GGUF models; no downloads and no changes to the model roster.
- Same three Passkey questions for each model, with old and new prompt variants: 18 sequential inferences total.
- Old variant: user question only, system message explicitly disabled for the A/B run.
- New variant: the repository's `PASSKEY_SYSTEM_PROMPT` (direct answer first, simple precise Polish, explain terms, separate facts from assumptions, avoid invented details, admit uncertainty, answer every part).
- CPU-only llamafile v0.10.6, 2 threads, context 2048, temperature 0, maximum 420 generated tokens, no response timeout.
- One model and one request at a time. Raw answers, durations, model filenames and SHA-256 hashes are in the accompanying JSON.

## Results

| Model | Old mean latency | New mean latency | Old mean answer length | New mean answer length | Assessment |
|---|---:|---:|---:|---:|---|
| SmolLM2 360M Q2_K | 51.03 s | 85.35 s | 628 chars | 735 chars | Both variants malformed and repetitive; new prompt was slower and did not improve clarity |
| Qwen2 0.5B Q4_K_M | 22.27 s | 25.93 s | 1359 chars | 1390 chars | Both variants repetitive and factually unreliable; new prompt did not improve correctness |
| Qwen2.5 0.5B Q2_K | 65.37 s | 57.74 s | 1359 chars | 980 chars | New prompt shortened output, but answers remained inaccurate/repetitive; one answer merely echoed the task |

## Conclusion

The exact repository system prompt is correctly sent to the models, but this A/B test shows no dependable improvement in Passkey answer quality for these three small quantized models. Shorter output is not evidence of better understanding. The new prompt sometimes changes response length or latency, but does not reliably prevent repetition, malformed Polish, incorrect definitions or incomplete answers.

None of the three models passes the Passkey answer-quality gate on this sample. Keep the prompt as the default test instruction for consistency, but do not treat it as a quality fix. Do not infer model admission, roster approval, or production readiness from this test. Any later candidate should be judged on correctness, completeness, non-repetition and clear uncertainty handling, not style alone.

## Operational state

- All 18 inference calls completed.
- The test-owned llamafile process exited after the final answer; TCP port 8088 is no longer listening.
- No model files were downloaded or modified.
- No production service, database, roster, cgroup limit, container setting or systemd configuration was changed.
- The pre-existing `arena-download-monitor` shell process (PID 3801320) was left untouched.
