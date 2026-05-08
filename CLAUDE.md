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
