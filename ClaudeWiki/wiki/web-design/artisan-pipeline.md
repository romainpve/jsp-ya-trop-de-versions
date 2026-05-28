---
updated: 2026-05-26
sources: [raw/2026-05-26-artisan-website-pipeline.md]
---

# Pipeline génération sites vitrines artisans

Workflow scraper Python → CLAUDE.md → Claude Code → Vercel pour produire des sites vitrines d'artisans français (vendus 800-1200€) en automatisé. Projet à `C:\Users\pavea\Desktop\Downloads\Claude Code\Projets\Web Desing\!workflow\`.

## What works

### Architecture : "script collecte, LLM analyse"
Séparation stricte des rôles. Le script Python (`company_scraper_bis.py`) fait UNIQUEMENT de la collecte : appels Google Places API, scraping Maps (avis + photos via Playwright), organisation fichiers, génération conducteurs (CLAUDE.md, COMPONENTS.md, PRODUCT.md, POST_DEPLOY.md), génération outils (preview_site.py, audit_content.py, finalize_site.py).

Claude lit `reviews_all.json` brut, regarde chaque photo, et décide. Pas de pré-extraction sémantique en Python.

### Marqueurs `__XXX__` + script de finalisation ré-exécutable
Pour les valeurs connues TARD dans le workflow (URL Vercel, email client artisan après vente) :
- Injection partout de `__SITE_URL__` et `__CLIENT_EMAIL__` (sitemap.xml, robots.txt, form `_next`, JSON-LD `url`, og:url, canonical)
- Script `finalize_site.py` dans le workspace : `python finalize_site.py --url X --email Y`
- **State file `.finalize_state.json`** qui tracke les dernières valeurs injectées → re-exécutable
- 2 passes : (1) marqueurs → nouvelles valeurs, (2) anciennes valeurs (du state) → nouvelles valeurs

Workflow concret : démo avec URL Vercel auto + email dev → vente → custom domain + email client. `finalize_site.py` appelé 2 fois, idempotent.

### Anti-hallucination par 3 mécanismes complémentaires
1. **Protocole de rédaction dans le brief** (§5bis de CLAUDE.md) : 3 modes autorisés (Précis sourcé / Générique métier honnête / Placeholder légal) + 1 interdit (Invention plausible)
2. **Tableau de seuils** : 0 mention = absent / 1 mention = exception (citation littérale max) / 2-3 = probable / ≥4 = vérifié
3. **Audit regex post-build** (`audit_content.py`) : scan `site/*.html` pour dates / prix / durées / noms / mentions garantie. Confronte aux sources brutes (profil.json, reviews_all.json) via recherche substring avec normalisation Unicode (NFKD + ASCII lower). Sort `HALLUCINATIONS_REPORT.md` [OK]/[?]/[X]. Exit 1 si ≥ 1 [X] → bloque deploy.

### PHASE 1 ordonnée : matériau AVANT décisions design
Séquence stricte dans CLAUDE.md §6 :
```
1.1 Inventaire ressources (ls + lire COMPONENTS.md)
1.2 Analyser photos (§4 gate bloquant) + écrire manifest.json
1.3 Lire reviews_all.json (§5bis)
1.4 Décisions design : CONCEPT + DESIGN.md + LAYOUT
1.5 Décisions composants (maintenant toutes données dispo)
```
Évite de définir une palette avant d'avoir vu les couleurs réelles des photos.

### §7 SPECS stratégiques avec exemples Bon/Mauvais
Pas que de la structure (où mettre quoi). Aussi du **contenu** : pour chaque section critique (Hero, Services, Zone d'intervention), exemples concrets de mauvais vs bons headlines/descriptions, avec source explicite (VoC reviews_all, profil.json, reviews_meta).

Insight : les LLMs calibrent bien sur exemples concrets. Règles abstraites seules → mode 2 générique creux.

### `reviews_inline.js` synchrone (jamais de fetch async)
Pour le contenu above-the-fold (avis sur la home). Le script Python génère `site/reviews_inline.js` qui set `window.SITE_REVIEWS = [...]` et `window.SITE_REVIEWS_META = {...}` directement. Composants Alpine.js utilisent `window.SITE_REVIEWS` au lieu de `fetch('reviews.json')`.

Bénéfice : (a) marche en `file://` (pas de CORS), (b) pas de race condition au screenshot Playwright (contenu vide pendant 200-500ms si async).

### `preview_site.py` avec scroll préliminaire
Avant chaque screenshot, le script Playwright fait `scrollTo(0, document.body.scrollHeight)` → `scrollTo(0, 0)` → wait 0.4s → screenshot. Ça active les IntersectionObservers de toutes les sections (animations `.reveal`, fade-in `.gallery-img`). Sans ça : toutes sections sous le fold blanches dans les captures.

### Structure `site/` séparée du workspace
Workspace racine = CLAUDE.md + outils + sources. `out_dir/site/` = HTML déployable + assets. Deploy : `cd site && vercel --prod`. Pas besoin de `.vercelignore`.

### Génération automatique outils dans workspace
À chaque scraper run, le script génère dans le workspace : `preview_site.py`, `audit_content.py`, `finalize_site.py`, `POST_DEPLOY.md`. Aucune dépendance externe à installer côté user.

## What to avoid

### Pré-extraction sémantique en Python (anti-pattern voc.json)
Tenté : `_FRENCH_FIRST_NAMES` liste 300 prénoms + `_QUALITY_ADJECTIVES` 50 adjectifs + `_SERVICE_KEYWORDS` par métier + 5 helpers `_extract_*` → 269 lignes. Retiré complètement. Pourquoi :
- Listes incomplètes (prénoms étrangers, accents variables)
- Le LLM fait confiance à l'extraction incomplète et ne lit plus les avis bruts
- Dette technique permanente
- Insulte la compétence du LLM

Règle : si le LLM peut faire mieux qu'un regex/dictionnaire, ne pas pré-mâcher en Python.

### Scraping fragile pour enrichissement (societe.com, DuckDuckGo)
Cloudflare bloque societe.com ~80% du temps. DDG rate-limit en batch. Quand ça marche : risque de matcher la mauvaise entité homonyme. Remplacer par placeholders explicites (`SIRET : [À compléter]`) + documenter dans POST_DEPLOY.md.

### Placeholders ambigus type "VOTRE-SITE.vercel.app"
Si un placeholder ressemble à du vrai contenu, un oubli passe inaperçu. Utiliser des marqueurs visuellement aberrants : `__SITE_URL__`, `__CLIENT_EMAIL__`. Si l'un survit au deploy → visible immédiatement sur le site.

### Hardcoder l'email/URL dev dans le code generator
`formsubmit_email = "paveau.romain@gmail.com"` hardcodé = tous les devis du client futur arrivent chez le dev. Utiliser des marqueurs + script de finalisation obligatoire.

### Scripts de finalisation non ré-exécutables
Si `finalize_site.py` remplace les marqueurs mais ne tracke pas les valeurs injectées, le 2e appel (custom domain après vente) ne trouve plus les marqueurs et ne fait rien. **Bug commercial fatal** (devis client → boîte dev). Solution : state file `.finalize_state.json`.

### Sections "Formules tarifaires" inventées
Le pattern `Saison / Confort / Premium` avec 3 cards tarifées est l'hallucination #1 du secteur. Tarifs jamais affichés publiquement par les artisans. **TOUJOURS supprimer** sauf si tarifs réels connus.

### Headlines génériques type métier
"Plombier rapide à Reims" = oubliable. Utiliser la VoC concrètement : prénom artisan + service spécifique + délai mentionné + zone réelle. Exemple : "Karim intervient le jour même sur toutes vos fuites à Reims" (4 éléments sourcés).

### Listes maintenues à la main
Prénoms français courants, adjectifs qualité, mots-clés par métier... évoluent constamment. Maintenance fragile. Préférer instruire le LLM à extraire à la lecture.

### Inflation de gaps imaginaires (sur-engineering)
Sur 50 "gaps potentiels" identifiés en simulation, typiquement 80% sont de la paranoïa : assumptions que le LLM va merder + mécanismes de garde-fou. Chaque mécanisme = code = bug potentiel = friction. Tri honnête : critique (casse en prod) / minor (UX) / faux gap (sur-engineering). Ne fixer que les vrais critiques + quelques docs.

### `fetch()` pour contenu critique above-the-fold
CORS en file://, race condition au screenshot. Préférer un fichier `xxx_inline.js` synchrone qui set `window.X = [...]` au chargement.

### Skill `$impeccable` / `$emil-design-eng` requis sans fallback
Si le user n'a pas les skills installés, Claude reste bloqué. Toujours mentionner un fallback manuel dans CLAUDE.md.

## Key patterns

1. **Script collecte, LLM analyse** : règle architecturale. Toute compréhension sémantique revient au LLM. Le script ne fait que appels API + scrape + organisation fichiers + génération conducteurs.

2. **Marqueurs + finalisation ré-exécutable** : pattern pour valeurs connues N étapes plus tard. Marqueurs aberrants visuellement + script avec state file qui tracke les dernières valeurs injectées + 2 passes (marqueurs + anciennes valeurs).

3. **Anti-hallucination par 3 modes + audit regex** : (a) protocole de rédaction dans le brief avec modes explicites, (b) seuils chiffrés sur mentions, (c) audit post-build qui flag les chiffres/noms absents des sources. Pas besoin d'un second LLM.

4. **Décisions design APRÈS analyse du matériau** : palette, layout, composants se décident avec les vraies photos + vrais avis sous les yeux, pas dans le vide.

5. **Specs section = structure + exemples Bon/Mauvais** : pour calibrer le LLM sur la qualité de copy attendue. Règles abstraites seules produisent du mode 2 générique.

6. **Synchrone > async pour above-the-fold** : `window.X` chargé via `<script src>` synchrone, jamais `fetch()`. Marche en file://, pas de race condition au screenshot.

7. **Screenshot Playwright = scroll préliminaire** : `scrollTo(bottom)` → `scrollTo(top)` → wait → screenshot. Active tous les IntersectionObservers.

8. **Separation workspace / déployable** : workspace racine = conducteurs + outils + sources, `site/` subfolder = ce qui va sur Vercel. `cd site && vercel --prod`.

9. **Outils générés dans workspace par le scraper** : `preview_site.py`, `audit_content.py`, `finalize_site.py`, `POST_DEPLOY.md` créés au scraping. Zéro setup côté user.

10. **Tri honnête des "gaps"** : critique (casse en prod) → fixer, minor (UX) → doc, sur-engineering paranoïaque → ne pas toucher. Typiquement 80% sont du dernier groupe.

## Subtilités déploiement / commercial

### Workflow démo → vente → custom domain
Vendre des sites artisans 1200€ par démo : on construit, on déploie sur URL Vercel auto, on appelle le client avec l'URL démo. Vente conclue → custom domain. Les 2 transitions sont gérées par 2 appels à `finalize_site.py`.

### FormSubmit.co activation par email destinataire
Combinaison (form action URL, destination email) → FormSubmit considère ça comme un form unique. Si on change l'email après une 1ère activation, **il faut re-activer** (re-soumettre un test, recliquer le lien envoyé au nouvel email). Documenter explicitement dans POST_DEPLOY.md.

### Pas de pre_deploy_check.py séparé
Rendu redondant par `finalize_site.py` qui garantit le replace par construction. Si finalize est passé : pas de marqueurs survivants possibles.

## Related

*(aucune page connexe pour l'instant)*
