Process all new sources in the Brain knowledge base.

Vault location: `C:\Users\pavea\Documents\Brain\`

Steps:
1. Read `C:\Users\pavea\Documents\Brain\wiki\log.md` to know which sources have already been ingested.
2. List all files in `C:\Users\pavea\Documents\Brain\raw\` (excluding the `assets/` subfolder and any already in log.md).
3. If no new files found, reply "Brain is up to date — no new sources to ingest." and stop.
4. For each new source file, follow the full ingest workflow from `C:\Users\pavea\Documents\Brain\CLAUDE.md`:
   a. Create `wiki/sources/[slug].md` — source summary page
   b. Create or update `wiki/concepts/[concept].md` for each extracted concept
   c. Create or update `wiki/entities/[entity].md` for each notable person/org/tool
   d. Create `wiki/synthesis/[topic].md` if meaningful cross-source connections emerge
   e. Update `wiki/index.md` with all new pages
   f. Append to `wiki/log.md`: `YYYY-MM-DD — Ingested: [source title] → [N concepts], [M entities]`
5. Report: "Ingested [N] source(s) — [X] concepts, [Y] entities, [Z] synthesis pages created/updated."

Ingest is idempotent: never re-process a source already in log.md.
