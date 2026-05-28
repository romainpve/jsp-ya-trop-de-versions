# ClaudeWiki — Human Reference

> This file is for human reading. Claude operates from `~/.claude/CLAUDE.md` which contains all instructions inline.

## What this is
Personal knowledge base built from Claude sessions. General-purpose — not tied to any domain or project. Captures patterns, lessons, mental models, and anti-patterns discovered during real work.

## Structure
```
ClaudeWiki/
  CLAUDE.md          ← this file (human reference)
  raw/               ← session debriefs, written by Claude on /wiki
    _current-session.md   ← staging file (mid-session notes, deleted on /wiki)
    YYYY-MM-DD-topic.md   ← completed debriefs
  wiki/              ← compiled knowledge pages, maintained by Claude
    index.md         ← master index, read by Claude at session start
    [category]/
      [topic].md
```

## Commands
- `/wiki` — write debrief for current session, update index
- `/wiki-compile` — integrate pending debriefs into wiki pages (run periodically)
- `/wiki-lint` — health check: orphans, contradictions, uncompiled debriefs

## Flow
```
Session happens
↓
Claude notes key moments to raw/_current-session.md (automatic)
↓
User runs /wiki
↓
Claude writes raw/YYYY-MM-DD-topic.md, updates index
↓
Periodically: /wiki-compile
↓
Wiki pages grow and interconnect
↓
Next session: Claude reads index + relevant pages before starting
```

## Conventions
- Page names: lowercase, hyphenated
- Categories: emerge from content, never pre-defined
- One solid page beats five stubs
- Every page must appear in index.md
- Every raw/ debrief must eventually be compiled (compiled: true)
