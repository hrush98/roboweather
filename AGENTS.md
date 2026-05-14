# Repository Agent Instructions

- After a large or multi-file change set, break the work into coherent commits and push the branch when verification passes.
- Keep generated runtime state out of commits unless explicitly requested. This includes live SQLite databases, logs, and bulky ad hoc research artifacts.
- Before committing, inspect `git status --short` and avoid staging unrelated user changes accidentally.
