---
name: start-thread
description: Create one bounded RoboWeather board thread with a single question, one next action, and a named closure output. Use when beginning substantial work that may span sessions, when an open question needs an explicit owner and handoff, or before implementation that is not already represented by an open board thread.
---

# Start Thread

1. Read `AGENTS.md`, `agent_loop/STATE.md`, `agent_loop/facts.json`, and `board/INDEX.md` in that order.
2. Search the open board for the same question. Resume the existing thread instead of duplicating it.
3. Choose one primary edge pillar: `information`, `settlement`, `execution`, or `costs-adverse-selection`. Use `cross-pillar` only for a genuine integration gate or project-governance change; it is not a fifth edge source.
4. Keep the question narrower than an implementation plan and scoped to the declared pillar. Split unrelated questions or different primary pillars.
5. Name one executable next action and the durable output that will close the thread.
6. Run:

```text
/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/agent_loop.py start-thread \
  --title "SHORT TITLE" \
  --question "ONE QUESTION" \
  --next-action "ONE EXECUTABLE ACTION" \
  --closure-output "NAMED DURABLE OUTPUT" \
  --pillar "information|settlement|execution|costs-adverse-selection|cross-pillar" \
  --owner "AGENT OR HUMAN"
```

Use the local checkout's documented `roboweather` Python path when `/home/maxrush` is unavailable. Respect the seven-open and three-active caps. Do not hand-create an ID or edit `board/INDEX.md`.
