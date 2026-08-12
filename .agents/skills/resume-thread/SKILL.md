---
name: resume-thread
description: Resume a parked or waiting RoboWeather board thread after regenerating machine facts and checking whether its recorded context changed. Use when continuing work from another agent or session, when the user names a T#### thread, or when selecting the next bounded item from board/INDEX.md.
---

# Resume Thread

1. Run the resume command before reading deep implementation history:

```text
/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/agent_loop.py resume-thread T####
```

2. Read the output, then read the thread and only the canonical documents it links.
3. If the command reports changed facts, inspect `agent_loop/facts.json` and revalidate the next action before executing it.
4. Confirm that the question remains open and still belongs to its recorded edge pillar. If the work now has a different primary pillar, close or park this bounded question and start a separate thread.
5. Close it instead of continuing if the durable output already exists and satisfies the closure condition.
6. Keep the thread's next action current whenever work changes direction.

The command enforces the three-active cap and refuses to reopen closed history. Open a new thread when a settled answer creates a genuinely new question.
