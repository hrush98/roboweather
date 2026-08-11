---
name: park-thread
description: Park or wait a RoboWeather board thread with a concise current answer, evidence references, and exactly one resumable next action. Use before ending a session with unfinished work, when an external condition must change, or when freeing an active slot without losing the reasoning trail.
---

# Park Thread

1. Do not park a vague status report. Record the best current answer and its uncertainty.
2. Cite exact files, commands, commits, database watermarks, or observed failures as repeatable evidence.
3. Choose `PARKED` when work can resume immediately or `WAITING` when an external condition must change.
4. Provide exactly one next action that another agent can execute without reconstructing the session.
5. Run:

```text
/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/agent_loop.py park-thread T#### \
  --answer "CURRENT ANSWER" \
  --evidence "EXACT EVIDENCE" \
  --next-action "ONE EXECUTABLE ACTION" \
  --status PARKED
```

Repeat `--evidence` as needed. Use `WAITING` only for a real external dependency, not because work is difficult.
