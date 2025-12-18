# Agent Contract (intraday-crypto-scanner)

## Prime directive
Make incremental changes that keep the scanner working at all times.

## Non-negotiables
- Do not refactor unrelated code “for cleanliness”.
- No large rewrites. Max ~200 LOC net change per PR/step unless explicitly authorized.
- Each step must compile/run.
- Each step must include a verification method (command + expected outcome).
- Preserve current runtime behavior unless the step explicitly changes it.

## Workflow per step
1) Read-only analysis of relevant files
2) Propose a plan for THIS step only (bullets, max 10)
3) List files you will touch
4) Implement minimal diff
5) Add/adjust tests or a smoke-check script
6) Summarize what changed + how to verify
7) Stop and wait for next instruction

## Output format required (every response)
### Summary
### Plan (this step)
### Files to change
### Patch
### How to verify
### Risks / rollback

## Guardrails
- If uncertain, ask for evidence from repo (file path / current behavior), not opinions.
- Prefer adding small new modules over modifying lots of existing ones.
- No new dependencies without approval.

## Acceptance definition
A step is “done” only if verification passes and behavior matches intent.
