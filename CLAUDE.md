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

## Iterative Contract and Implementation Hardening

Before starting multi-agent coding, treat documentation as an executable contract. During coding, treat the implementation as the executable form of that contract. The main agent must fork multiple independent read-only review agents for substantial contract or implementation changes; self-review alone is not enough because the main agent is biased by the current conversation, its own recent edits, and the assumptions it already accepted. Use this loop when preparing or changing cross-module work:

1. Read the authority documents and identify blocking ambiguities.
2. If the contract is unclear, fix the contract before changing implementation.
3. Fork multiple independent read-only review agents from different module perspectives. For substantial cross-module changes, use at least two reviewers with different ownership perspectives.
4. Treat each review as useful but potentially incomplete.
5. Merge confirmed findings into a ranked blocker list.
6. Fix blockers surgically in the source-of-truth docs or implementation files.
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

- Prefer fixing the source-of-truth document over adding migration notes.
- Do not preserve old names unless explicitly needed.
- Do not start implementation while unresolved blockers remain.
- Distinguish blocking interface ambiguity from non-blocking wording polish.
- Record the final decision in the document, not only in chat.
- When implementation reveals a contract gap, stop broad coding, update the contract, then resume implementation.
- Keep code fixes surgical: address the confirmed blocker without opportunistic refactors.
- After code edits, re-run targeted searches for stale names and at least one verification command relevant to the changed boundary.

When a review agent finds a blocker, do not assume the fix is complete after one edit. Re-run targeted searches and fork multiple final independent read-only review agents when the blocker affects shared contracts, module boundaries, serialization, caching, runtime state, or public behavior. Interface blockers often move from obvious naming conflicts to deeper type ownership, cache semantics, serialization contracts, or runtime state propagation. Implementation blockers often move from compile-time errors to behavioral mismatches, missing edge-case tests, and stale call sites.
