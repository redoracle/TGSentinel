---
applyTo: "**"
---

Prompt: “Refactor This Huge File into Modular Structure”

You are a senior software engineer and refactoring specialist.
Your task is to analyze a large, monolithic source file, discover logical modules that can be extracted, and then propose a step-by-step modularization plan following best practices in software design, maintainability, and testability.

⸻

1. Context
   • Programming language: python
   • Project type / architecture (if known): <e.g., backend service, REST API, CLI tool, library, etc.>
   • Main responsibility of this file: <short description of what this file is supposed to do>

If anything is unclear from the code, explicitly state your assumptions.

⸻

2. Input File
   You will be given one large source file that is currently doing too many things (functions, classes, helpers, constants, API calls, business logic, etc.).

Treat this input as:

“A legacy God-object / God-file that must be split into coherent, modular units.”

⸻

3. Your Goals
   1. Identify modules that could be extracted (current or potential “exportable units”).
   2. Define clear boundaries (what each module is responsible for, what it depends on, what it exposes).
   3. Minimize coupling and maximize cohesion.
   4. Provide a concrete, incremental refactoring plan that I can follow step by step.
   5. Preserve behavior – your plan must be refactor-oriented, not feature-changing.

⸻

4. Analysis Tasks
   On the given file, perform these steps: 1. High-level overview
   • Summarize in a few bullet points what this file currently does.
   • List the main responsibilities it mixes together (e.g., I/O, domain logic, validation, DB access, formatting, etc.). 2. Identify logical clusters / modules
   • Group functions, classes, and constants into logical clusters (potential modules) based on:
   • Responsibility (what they do)
   • Shared data structures
   • Shared dependencies
   • For each cluster, give:
   • A proposed module name (e.g., user_repository, order_validator, telegram_client, config_loader).
   • A short description (1–3 sentences).
   • The list of functions/classes that belong there (use exact names). 3. Determine public API vs internal details
   For each proposed module:
   • List which functions/classes should be exported / public (used by other modules).
   • List which elements should remain internal/private.
   • Explain why (e.g., “only used in X”, “implementation detail of Y”, “core domain abstraction”). 4. Dependency & coupling analysis
   • Describe which modules depend on which others (current and ideal).
   • Identify undesired couplings (e.g., business logic depending directly on low-level I/O).
   • Suggest how to invert or reduce dependencies, if useful (e.g., interfaces, adapters).

⸻

5. Refactoring Plan (Step-by-Step)
   Produce a concrete, ordered refactoring plan that can be executed incrementally.
   Structure it like this: 1. Phase 1 – Preparation (safe changes only)
   • Add missing types / docstrings / comments if needed for clarity.
   • Normalize naming, small extractions (pure helpers) without changing semantics.
   • Add or adjust tests (if present) to lock in current behavior. 2. Phase 2 – Extract modules
   For each module:
   • Module <module_name>
   • Files to create:
   • path/to/<module_name>.{ext}
   • (if needed) **init**.py / index file / barrel exports.
   • Move the following items: <functionA>, <classB>, <CONSTANT_C>.
   • Replace old imports/usages with the new module import path.
   • Notes:
   • Potential breaking changes.
   • Any required refactor to function signatures or data structures.
   • Tests to adjust/add.
   Repeat for every proposed module until the original file becomes thin and focused. 3. Phase 3 – Clean up & harden
   • Remove dead code / unused functions.
   • Ensure there is a clear public API layer (e.g., index file, service layer).
   • Confirm the main entry points are simple to understand and well isolated.
   • Propose future improvements (e.g., dependency injection, better error handling, more granular modules) while keeping them optional.

For each phase, use checklists and explicit instructions that I can follow directly.

⸻

6. Output Format
   Return your answer in this structure: 1. Overview
   • Short description of the file.
   • List of mixed responsibilities. 2. Proposed Module Map
   • Table with columns: Module Name | Responsibility | Public API | Internal Items | Notes. 3. Dependency Notes
   • Bullet list describing key dependencies and coupling issues. 4. Step-by-Step Refactoring Plan
   • Phases with checkbox lists and explicit file/function moves. 5. Risks & Edge Cases
   • What to watch out for (e.g., globals, shared state, side effects, weird imports). 6. Final Target Structure (Example)
   • Show an example of the final folder/file layout.

⸻

7. Important Constraints
   • Do not change behavior in your plan – refactor only.
   • Prefer small, incremental steps over “big bang” rewrites.
   • Follow language-specific best practices:
   • <add here: e.g., Python: PEP8, clear package boundaries; TypeScript: barrel files, types in types.ts, etc.>
   • If something is ambiguous, call it out explicitly and state your assumption.
