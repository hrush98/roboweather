---
name: close-thread
description: Close a RoboWeather board thread after its question is answered, naming the durable output and preserving the result in append-only closed history. Use when implementation and verification finish, a hypothesis is rejected or superseded, or a bounded investigation reaches a defensible no-change conclusion.
---

# Close Thread

1. Verify that the thread's single question has a defensible, pillar-scoped answer. State what was or was not proved for that pillar without treating infrastructure completion as edge.
2. Produce or update the named closure output before closing. A valid output may be code and tests, a canonical document update, a dated reference, or an explicit no-change verdict.
3. Ask whether the work revealed a transferable concept, failure mechanism, experience, or design intuition. If so, use `$capture-learning` and link this thread.
4. Update `docs/current-trading-system-audit.md`, the roadmap, live journal, and changelog only when their documented triggers apply.
5. Run:

```text
/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/agent_loop.py close-thread T#### \
  --outcome "SETTLED ANSWER" \
  --durable-output "PATH, COMMIT, OR EXPLICIT NO-OUTPUT VERDICT" \
  --evidence "FINAL VERIFICATION"
```

6. Run `scripts/agent_loop.py stop` before the final handoff.

Do not close merely because a session or token budget ended. Park unfinished work instead. Never rewrite a closed thread; open a new thread that links to the superseded result.
