Run a health check on the Brain knowledge base.

Vault location: `C:\Users\pavea\Documents\Brain\`

Steps:
1. Read `C:\Users\pavea\Documents\Brain\wiki\index.md` and all wiki pages.
2. Read `C:\Users\pavea\Documents\Brain\wiki\log.md`.
3. Check for:
   - **Orphan pages**: wiki pages with no wikilinks pointing to them
   - **Broken wikilinks**: [[references]] to pages that don't exist
   - **Unprocessed sources**: files in `raw/` not referenced in `log.md`
   - **Missing pages**: concepts or entities mentioned across pages but without their own dedicated page
   - **Contradictions**: conflicting claims between pages
   - **Thin pages**: pages with less than 3 meaningful bullet points (candidates for merging)
4. Report all findings clearly, grouped by category.
5. For each issue, offer to fix it. Wait for confirmation before making changes.
