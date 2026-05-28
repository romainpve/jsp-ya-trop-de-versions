Compile all unprocessed session debriefs into the wiki.

Steps:
1. Read `C:\Users\pavea\Documents\ClaudeWiki\wiki\index.md`.
2. List all files in `C:\Users\pavea\Documents\ClaudeWiki\raw\` where `compiled: false`.
3. If none, reply "Wiki is up to date — nothing to compile." and stop.
4. For each uncompiled debrief:
   a. Find relevant existing wiki pages via `index.md`.
   b. If a relevant page exists: integrate new insights, update any contradicted content, add the raw filename to the page's `sources` frontmatter.
   c. If no relevant page exists and the topic deserves its own page: create `wiki/[category]/[topic].md`. Let the category emerge naturally from the content — do not force pre-defined folders.
   d. Set `compiled: true` in the raw debrief file.
5. Update `C:\Users\pavea\Documents\ClaudeWiki\wiki\index.md` with any new pages.
6. Report: "Compiled [N] debriefs — [X] pages created, [Y] pages updated."

Wiki page format to use:
```markdown
---
updated: YYYY-MM-DD
sources: [raw/filename.md, ...]
---

# [Topic]

## What works
...

## What to avoid
...

## Key patterns
...

## Related
[[other-page]]
```
