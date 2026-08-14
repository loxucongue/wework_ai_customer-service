# V3 Golden Set And Critic Usage

## What It Is

The V3 golden set is an offline evaluation standard, not a runtime reply library.

It contains reviewed multi-turn cases with:

- customer goal
- must-answer points
- acceptable postures
- required or acceptable content assets
- required structural deliveries
- forbidden actions and claims
- reference reply direction

The reference examples are examples of acceptable direction. They are not standard answers and must not be matched by text similarity.

## What It Must Not Do

The golden set and Critic must not be imported by:

- Shared Context
- Gate
- Tool Planner
- Join
- Reply
- fact audit
- production runtime prompts

They also must not generate Python keyword branches. If a case fails, fix context, facts, assets, prompt structure, or model choice first.

## Evaluation Flow

1. Run V3 on approved offline cases and save a result JSON with per-case:
   - `case_id`
   - Gate candidate content IDs
   - Reply selected content IDs
   - delivered asset IDs
   - final `reply_messages`
   - optional offline Critic status
2. Run:

```powershell
PYTHONPATH=ai_paths python ai_paths/scripts/evaluate_v3_trusted_golden.py `
  --golden workflow_tests/fixtures/v3_trusted_golden_set_v1.json `
  --results .tmp_runtime/v3_golden_results.json `
  --output .tmp_runtime/v3_golden_evaluation.json
```

3. Review:
   - Gate Recall
   - False Nomination
   - Reply Adoption
   - False Adoption
   - Delivery Completion
   - forbidden action hits
   - first inquiry payment card rate
   - Critic calibration and holdout metrics

## Critic Boundary

Critic is offline-only.

It may evaluate whether a generated reply satisfies the approved case standard. It may not:

- modify a customer reply
- block a customer reply
- freeze customer state
- write business memory
- decide sales rhythm

The purpose is diagnosis: identify whether the failure belongs to Gate recall, Reply adoption, delivery completion, factual safety, or sales rhythm.

## Why This Does Not Turn V3 Into A Matcher

The runtime model never sees the golden cases. The golden set only judges outputs after the model has already replied in an offline run.

This keeps Reply as the sales brain while still making quality measurable.
