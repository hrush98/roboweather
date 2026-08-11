---
name: capture-learning
description: Capture a durable, human-oriented RoboWeather learning card from a failure, concept, design decision, or lived engineering experience. Use when work reveals transferable system-design intuition, a surprising failure mechanism, a concept the user wants to understand, or a lesson worth deliberately revisiting later.
---

# Capture Learning

1. Capture transferable understanding, not an ordinary bug report or task status. Link the originating `T####` thread when one exists.
2. Separate the concrete historical incident from the interpretation. Preserve what happened even if the explanation later changes.
3. Explain the concept and intuition in plain language, then connect it to a general pattern and RoboWeather practice.
4. Ask one or more questions worth revisiting and choose a real review date.
5. Run:

```text
/home/maxrush/miniconda3/envs/roboweather/bin/python scripts/agent_loop.py capture-learning \
  --title "TRANSFERABLE IDEA" --kind DESIGN --origin T#### \
  --why "WHY IT MATTERED" --happened "CONCRETE INCIDENT" \
  --concept "UNDERLYING CONCEPT" --intuition "PLAIN-LANGUAGE INTUITION" \
  --pattern "WHERE ELSE IT APPLIES" --application "ROBOWEATHER APPLICATION" \
  --questions "WHAT TO REVISIT" --evidence "EXACT SOURCE" \
  --revisit-on YYYY-MM-DD
```

Choose `CONCEPT`, `FAILURE`, `DESIGN`, or `EXPERIENCE`. Do not use a learning card as trading authority or silently turn provisional intuition into a canonical conclusion.
