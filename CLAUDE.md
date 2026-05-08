# CLAUDE.md

## Documentation Hygiene

When editing documentation, write the final desired state only.

Do not include:

- Legacy design explanations
- Deprecated parameter names
- Old-vs-new migration notes unless explicitly requested
- Explanations of why something was removed
- Placeholder examples that mention removed options
- Warnings about designs that no longer exist

If a previous name, option, or behavior has been removed, the final document should usually not mention it at all.

Treat documentation as the source of truth for the current design, not as a changelog. If historical context is useful, mention it in the chat response instead of the final documentation, unless explicitly asked to preserve it.

Before finishing a documentation edit, search the edited files for obsolete terms and remove them unless migration notes were explicitly requested.

## Multi-Agent Workflow

Use multiple agents for complex cross-file work when responsibilities can be split by file set or topic. Good fits include documentation consistency rewrites, pre-implementation specification cleanup, cross-module API alignment, and read-only reviews from multiple perspectives. Inspection and review tasks are especially good fits because multiple agents can examine the same files from different angles without write conflicts, then the main agent can merge and rank findings. Do not use multi-agent editing for small single-file changes, tightly coupled algorithm work, or cases where several agents would need to edit the same large section.

After the main agent completes the initial project-structure read, it must explicitly consider whether multiple agents are warranted before editing or concluding on cross-file work. Use multiple independent read-only review agents when the task affects cross-file or cross-module contracts.

Strong triggers include:

- Changes or reviews spanning two or more authority documents.
- CLI behavior, mode rules, runtime flags, scheduling, cache semantics, paths, config/context fields, shared schemas, artifacts interfaces, public outputs, or cross-document terminology.
- Documentation consistency checks where the same field, command, stage, path, or behavior appears in multiple files.
- Preparing or changing implementation for shared contracts such as `core/`, CLI, cache/config/context, schemas, artifacts, or pipeline boundaries.
- Review findings that affect shared contracts, serialization, caching, runtime state, module boundaries, or public behavior.

For substantial contract work, use at least two read-only reviewers with different perspectives, such as:

- core/shared types/config/cache/context
- CLI/runtime flags/scheduling
- audio/visual/merge pipeline contracts
- documentation consistency and user-facing examples

If the main agent decides not to use multiple agents for a cross-file task, it should briefly state why. Do not use multiple agents for small single-file edits, typo fixes, wording-only cleanup, tightly coupled algorithm work, or unclear requirements that first need clarification.

Review agents must not edit files unless explicitly assigned non-overlapping ownership. Their prompts must specify files to read, review perspective, obsolete terms or conflicts to look for, and require findings with file paths and line numbers.

Preferred workflow:

1. Split the task into non-overlapping file or topic ownership before launching agents.
2. Give each agent enough context to read first: the target files, upstream specification files, downstream consumer files, and relevant entry-point or authority documents.
3. Restrict each editing agent to an explicit file list. If a shared file must be changed, let one agent own it or have agents return suggestions for the main agent to apply.
4. Include the final terminology, API signatures, field names, and design decisions in each prompt. Do not let agents infer unresolved decisions independently.
5. Require each editing agent to report changed sections and any synchronization points for other files.
6. Treat parallel agent reports as potentially stale because they may be based on earlier workspace snapshots. The main agent must verify against the current files before acting on reported follow-ups.
7. After merging agent work, run project-wide searches for obsolete terms, old API signatures, old field names, and conflicting examples.
8. After the main agent merges and normalizes the agent outputs, launch one or more final read-only review agents for cross-document or cross-module consistency. The review prompt must forbid edits and require file paths with line numbers.
9. The main agent fixes any confirmed findings from the final review, then re-runs targeted searches for the corrected terms or APIs.
10. The main agent summarizes both changes and verification.

Agent prompts should state:

- The agent's role and goal.
- Files that must be read before editing.
- Files the agent is allowed to modify.
- Final desired terminology and API contracts.
- Explicit obsolete terms or designs that must not remain.
- Whether the task is edit mode or read-only review mode.
- The required final report format.

## Mandatory Review-Fix Loop For Every Change

Any task that modifies files must include an explicit review-fix loop. This applies to code, documentation, configuration, tests, scripts, prompts, examples, and generated project files. Do not treat review as an optional final polish step.

Minimum loop for every modification:

1. Inspect the relevant current files and authority documents before editing.
2. Make the smallest correct change.
3. Review the changed files against the user request and applicable project rules.
4. Fix confirmed issues.
5. Re-review the changed surface after the fix.
6. Run the smallest relevant verification command or targeted search.
7. Stop only when the latest review after the latest fix has no confirmed blockers.

For trivial single-file edits, the review may be a local self-review, but it must still happen after the edit and after any fix.

Use independent read-only reviewers when any of these apply:

- The change touches more than one file.
- The change affects behavior, not just wording.
- The change affects CLI behavior, runtime flags, scheduling, cache semantics, paths, config/context fields, schemas, artifacts, serialization, public outputs, scripts, prompts, tests, or docs that define current behavior.
- The first review finds a blocker.
- The user explicitly asks for careful review or end-to-end completion.

Use multiple independent read-only reviewers when any of these apply:

- The change crosses module boundaries.
- The change affects shared contracts or public behavior.
- The change affects `core/`, CLI, cache/config/context, schemas, artifacts, serialization, pipeline boundaries, or user-facing outputs.
- The change is large enough to require agents or parallel work.

Loop rules:

- Review must happen after implementation, not only before.
- If review finds blockers, fix them and then re-review. Do not stop after the fix.
- If a fix affects a shared contract, public behavior, or module boundary, re-run independent read-only review after the fix.
- When documentation and implementation conflict, treat the documentation as the source of truth by default. Fix the implementation to satisfy the documented contract, even if that requires additional implementation work.
- Only change documentation instead of implementation when the documentation is internally contradictory, impossible to implement, explicitly superseded by the user, or clearly stale status text rather than a current contract. In that case, record the final contract in documentation before or together with the implementation fix.
- Tests, compile checks, CLI smoke tests, type checks, import checks, and targeted searches are verification. They do not replace review.
- A subagent implementation report is not proof of correctness. The main agent must verify or request read-only review of the changed surface.
- Do not summarize work as complete until the latest review after the latest fix reports no confirmed blockers and relevant verification passes.
- Keep fixes surgical. Do not use review findings as an excuse for opportunistic refactoring.

Required reporting for non-trivial modifications:

- `Review round N: found X blockers.`
- `Fix round N: fixed blockers A, B, C.`
- `Verification round N: passed/failed commands ...`
- `Re-review round N: no blockers / found new blockers ...`

Final responses for modified files must include:

- Number of review-fix rounds completed.
- Blocking findings fixed.
- Latest review result.
- Verification commands and results.
- Any non-blocking residual risks.

## Iterative Contract and Implementation Hardening

Before starting multi-agent coding, treat documentation as an executable contract. During coding, treat the implementation as the executable form of that contract. The main agent must fork multiple independent read-only review agents for substantial contract or implementation changes; self-review alone is not enough because the main agent is biased by the current conversation, its own recent edits, and the assumptions it already accepted. Use this loop when preparing or changing cross-module work:

1. Read the authority documents and identify blocking ambiguities.
2. If the contract is unclear, fix the contract before changing implementation.
3. Fork multiple independent read-only review agents from different module perspectives. For substantial cross-module changes, use at least two reviewers with different ownership perspectives.
4. Treat each review as useful but potentially incomplete.
5. Merge confirmed findings into a ranked blocker list.
6. Fix blockers surgically in implementation files to satisfy the source-of-truth docs, unless the documentation exception below applies.
7. Search for obsolete terms, old field names, stale examples, and conflicting API signatures.
8. Repeat review after each substantial contract change.
9. For code changes, run targeted tests, type checks, import checks, or smoke tests that match the changed surface.
10. Stop only when forked independent read-only review reports no blocking interface conflicts and the relevant verification passes.
11. Then continue or start coding agents with explicit file or module ownership.

Useful review perspectives:

- shared types, configuration, cache, and runtime context
- external service boundaries and adapter interfaces
- upstream, downstream, and aggregation pipeline contracts
- entry-point scheduling, runtime flags, and error handling
- cross-document terminology, examples, and serialization formats

During this process:

- Prefer fixing implementation to match the source-of-truth document. Fix the document only when the contract itself is wrong, contradictory, impossible, stale status text, or explicitly superseded by the user.
- Do not preserve old names unless explicitly needed.
- Do not start implementation while unresolved blockers remain.
- Distinguish blocking interface ambiguity from non-blocking wording polish.
- Record the final decision in the document, not only in chat.
- When implementation reveals a contract gap, stop broad coding, update the contract, then resume implementation.
- Keep code fixes surgical: address the confirmed blocker without opportunistic refactors.
- After code edits, re-run targeted searches for stale names and at least one verification command relevant to the changed boundary.

When a review agent finds a blocker, do not assume the fix is complete after one edit. Re-run targeted searches and fork multiple final independent read-only review agents when the blocker affects shared contracts, module boundaries, serialization, caching, runtime state, or public behavior. Interface blockers often move from obvious naming conflicts to deeper type ownership, cache semantics, serialization contracts, or runtime state propagation. Implementation blockers often move from compile-time errors to behavioral mismatches, missing edge-case tests, and stale call sites.
