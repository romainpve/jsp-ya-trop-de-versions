Save a wiki checkpoint for work done since the last /wiki call (or since session start if first time). Does not close the session.

Steps:
1. Read `C:\Users\pavea\Documents\ClaudeWiki\raw\_current-session.md` if it exists — it contains mid-session notes captured since the last checkpoint.
2. Write a debrief covering only the work done since the last /wiki call to `C:\Users\pavea\Documents\ClaudeWiki\raw\YYYY-MM-DD-[topic-slug].md` (use today's actual date). Use this format:

```
---
date: YYYY-MM-DD
topic: [short label]
project: [project name or "general"]
compiled: false
---

## Context
What was the task or problem during this period.

## What worked
Specific solution or approach. Enough detail to reproduce. Include code if relevant.

## What failed
What was tried and why it didn't work.

## Key pattern
The reusable insight in 1-3 sentences. Written as if explaining to a future Claude with zero memory of this session.

## Saves next time
What this avoids: dead ends, wrong assumptions, wasted tokens.
```

3. Delete `_current-session.md` after incorporating its content — it resets for the next checkpoint.
4. Update `C:\Users\pavea\Documents\ClaudeWiki\wiki\index.md` — add any new topics with one-line descriptions.
5. Reply with exactly one line: "Wiki checkpoint — [topic]."

The session continues normally after this. /wiki can be called multiple times in the same session.
Do not compile wiki pages here. That is /wiki-compile.
