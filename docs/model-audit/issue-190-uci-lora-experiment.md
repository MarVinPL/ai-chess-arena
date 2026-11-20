# Issue #190 — UCI-format LoRA experiment

This experiment is restricted to producing exactly one UCI move. It does not train
strategy, Elo, Passkey answers, production behavior, or the arena roster.

`scripts/build_uci_format_dataset.py` creates 600 self-authored CC0 examples using
`python-chess`: 500 train, 50 validation, 50 test. Positions are globally FEN-
deduplicated and source-game groups do not cross splits. The label is a seeded
member of the legal-move list, explicitly not an engine-best move.

There is one configuration and one separate LoRA adapter directory per base family:
SmolLM2-360M-Instruct, Qwen2-0.5B-Instruct, and Qwen2.5-0.5B-Instruct. Expected
upstream licenses are Apache-2.0, but training may proceed only after the downloaded
snapshot's model card/license is checked and recorded.

## Required execution evidence

For each model, record a baseline and an adapter A/B report on the immutable test
split: exact-single-UCI rate, legal-move rate, extra-text rate, empty rate, and
latency. Record both raw generation and the runner/GBNF constrained path separately.
Do not report a constrained parser extraction as raw-model behavior. Do not publish
base weights or adapters without license, size, and owner approval.

## Current status

Pipeline preparation only. No base weights, adapter, inference result, production
change, roster change, or deployment is included in this commit.
