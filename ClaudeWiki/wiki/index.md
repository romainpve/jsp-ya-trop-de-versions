# ClaudeWiki — Index

> Master index. One line per page. Claude reads this first to navigate without loading everything.
> Format: `- [[relative/path]] — one-line description`

## Web design

- [[web-design/artisan-pipeline]] — Pipeline complet scraper Python → CLAUDE.md → Claude Code → Vercel pour sites vitrines artisans français. Principes : "script collecte, LLM analyse", marqueurs `__XXX__` + finalize ré-exécutable avec state file, anti-hallucination 3-modes + audit regex, decisions design après matériau, §7 SPECS stratégiques avec exemples.
- [[web-design/haristoy-full-build]] — Exécution complète end-to-end du pipeline pour Haristoy Plomberie Bayonne : analyse 74 photos (PIL thumbnails), VoC extraction 69 avis, design tokens OKLCH, 8 pages HTML, audit regex fix (`[À-Ÿ]`→`[À-ÖØ-Þ]`), logo PNG alpha, deploy Vercel + finalize.

## Deploy

- [[deploy/vercel-static-finalize]] — Pattern finalize_site.py pour sites statiques Vercel : marqueurs `__SITE_URL__`/`__CLIENT_EMAIL__`, state file ré-exécutable, alias URL stable vs deployment URL changeante.

---
*Last updated: 2026-05-27.*
