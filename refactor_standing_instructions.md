# LLM Coding Agent Guardrails  
**Project:** intraday-crypto-scanner

This document defines **non-negotiable rules** for any LLM coding agent working on this codebase.  
The goal is to implement a state-based attention engine **incrementally**, without destabilizing or refactoring the existing scanner.

---

## 🔒 Hard Rules (Must Follow)

- **DO NOT rewrite existing pattern detectors**
  - No threshold tuning
  - No logic reinterpretation
  - No “cleanup refactors”

- **DO NOT refactor analyzer internals unless explicitly instructed**
  - `core/analyzer.py`, `_analyzer.py`, historical analyzer variants are considered *stable*
  - Treat them as black boxes unless told otherwise

- **Prefer additive changes**
  - Add new modules and folders
  - Avoid editing many existing files in one phase

- **All phases must be runnable**
  - No half-wired abstractions
  - No TODOs that break execution

- **Feature-flag all behavioral changes**
  - Old behavior must be recoverable
  - Default flags should preserve current production behavior

- **No side effects in core logic**
  - State inference must be deterministic
  - Same inputs → same outputs

---

## 🧠 Design Constraints (Architecture)

- Patterns are **events**, not decisions
- State logic sits **above** pattern detection
- Nothing may skip layers:
  - Patterns → State → Alert Permission
- HTF context may be stubbed initially but **must exist as an input**
- HTF can downgrade state but **must never upgrade**

---

## 🧪 Testing Requirements

- All new **pure functions** must have unit tests
- Tests should live under `tests/`
- Focus on:
  - State transitions
  - Invariants (e.g. HTF never upgrades)
  - TTL and persistence behavior

---

## 📦 Required Output Format (Every Phase)

Before writing code, output the following:

1. **Files to change**
   - Explicit list of files (existing + new)

2. **Patch plan**
   - Bullet-point steps
   - Clear sequencing

3. **Code changes**
   - Minimal, scoped diffs
   - No unrelated edits

4. **How to run / test**
   - Commands or instructions to validate behavior

---

## 🚫 Anti-Patterns (Do Not Do These)

- “While I’m here” refactors
- Renaming existing concepts for aesthetics
- Introducing new indicators without contracts
- Collapsing multiple phases into one
- Making state logic depend on pandas or runtime globals

---

## ✅ Success Criteria

- Existing scanner behavior remains intact until explicitly gated
- State logic can be enabled/disabled via config
- Each phase can be reviewed, tested, and reverted independently
- Code structure makes it **hard to regress into indicator soup**

---

**If a change violates any rule above, stop and ask for clarification.**  
Silence is preferable to creative destruction.
