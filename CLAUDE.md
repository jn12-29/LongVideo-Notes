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
