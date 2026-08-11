---
name: revisit-learning
description: Revisit and mature a RoboWeather learning card by appending reflection, connecting it to other concepts, and recording a practice or action. Use when a learning card becomes due, the user asks to review lessons or concepts, new evidence changes an earlier explanation, or a captured idea is ready to become integrated or explicitly superseded.
---

# Revisit Learning

1. Read `learning/INDEX.md`, then the selected `L####` card and its originating thread or evidence.
2. Preserve the original incident and explanation. Append a dated reflection instead of rewriting history.
3. Record one new conceptual connection and one practice, experiment, or behavior the learning implies.
4. Select maturity deliberately:
   - `REVISIT` when understanding remains provisional or another review is useful.
   - `INTEGRATED` when the user can connect and apply the idea reliably.
   - `SUPERSEDED` when a better explanation replaces it; capture the replacement separately.
5. Run:

```text
/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/agent_loop.py revisit-learning L#### \
  --reflection "WHAT CHANGED IN MY UNDERSTANDING" \
  --connection "RELATED CONCEPT OR EXPERIENCE" \
  --action "PRACTICE, EXPERIMENT, OR DESIGN RULE" \
  --status REVISIT --revisit-on YYYY-MM-DD
```

Revisiting is for learning and synthesis. Update canonical system documents separately only if the evidence changes an actual repository conclusion.
