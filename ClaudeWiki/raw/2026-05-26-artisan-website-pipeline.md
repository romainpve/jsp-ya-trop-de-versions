---
date: 2026-05-26
topic: artisan-website-pipeline
project: web-design-artisans
compiled: true
---

## Context

Refonte profonde du pipeline `company_scraper_bis.py` (à `C:\Users\pavea\Desktop\Downloads\Claude Code\Projets\Web Desing\!workflow\`) qui génère des sites vitrines pour artisans français français (vendus 800-1200€). Le workflow : scraper Google Places + Maps → générer un workspace pour Claude Code (CLAUDE.md + ressources) → Claude construit le site dans `site/` → deploy Vercel.

Plusieurs sessions accumulées sur : structure deploy/démo/vente, finalize_site.py pour URL/email post-vente, anti-hallucination (avis comme source de vérité), réorganisation PHASE 1, guidage stratégique du contenu (headlines, services, zones).

## What worked

### 1. Principe "le script collecte, Claude analyse"
Le pivot le plus important du projet : **toute pré-analyse sémantique faite par Python (voc.json avec regex/keyword matching) déresponsabilise Claude et introduit des biais** (listes de prénoms incomplètes, adjectifs ratés). Solution : le script Python ne fait QUE de la collecte (API + scrape + organisation fichiers). Claude lit `reviews_all.json` brut, analyse, décide. Même principe que pour les photos : pas d'OpenCV, Claude regarde et choisit.

Concrètement : suppression complète de `generate_voc_json()`, des constantes `_FRENCH_FIRST_NAMES`/`_QUALITY_ADJECTIVES`/`_SERVICE_KEYWORDS`, et de tous les helpers `_extract_*`. -269 lignes Python. CLAUDE.md §5bis recadré : "Lis reviews_all.json en entier, extrais mentalement les faits".

### 2. Marqueurs `__SITE_URL__` + `__CLIENT_EMAIL__` avec finalize_site.py ré-exécutable
Le client n'est connu qu'APRÈS la vente (workflow démo : on construit, on déploie sur URL Vercel auto, on appelle le client, on vend, on configure son custom domain).

Solution : marqueurs uniques injectés partout (sitemap.xml, robots.txt, form `_next`, JSON-LD url, og:url, canonical) + script `finalize_site.py` généré dans le workspace qui prend `--url` et `--email`, remplace les marqueurs.

**Subtilité critique** : `finalize_site.py` doit être **ré-exécutable** car appelé 2 fois (phase démo avec URL Vercel auto + email dev, puis phase finale avec custom domain + email client). Au 2e appel, les marqueurs n'existent plus → il faut aussi remplacer les anciennes valeurs.

Implémentation : fichier `.finalize_state.json` dans le workspace qui tracke ce qui a été injecté la dernière fois. Le script fait 2 passes : (1) marqueurs → nouvelles valeurs, (2) anciennes valeurs (du state) → nouvelles valeurs. Idempotent. Sans ce fix, **les devis du client continuaient à arriver chez le dev après la vente** (bug commercial fatal).

### 3. PHASE 1 réordonnée : photos AVANT décisions design
Bug initial : §6 PHASE 1 demandait `DÉCISION COMPOSANTS` + `CONCEPT DESIGN` AVANT l'analyse photos. Donc :
- BeforeAfterSlider décidé sans avoir vu les photos
- Palette définie sans connaître les vraies couleurs du matériau
- Claude faisait des choix au pifomètre puis devait rétro-ajuster

Fix : restructurer PHASE 1 en 5 étapes claires :
```
1.1 Inventaire ressources (ls + lire COMPONENTS.md)
1.2 Analyser photos (§4 gate) + manifest.json
1.3 Lire reviews_all.json (§5bis)
1.4 Décisions design (§4ter — maintenant avec le matériau)
1.5 Décisions composants (maintenant avec toutes les données)
```

### 4. §7 SPECS PAR PAGE : passer de structurel à stratégique
Avant : §7 disait "Section Services = 3 cards icône+titre+description". Structurel. Claude écrivait des descriptions génériques creuses ("Notre équipe d'experts à votre service").

Après : §7 ajoute des **exemples Bon/Mauvais concrets** :
- Section 2 Hero : 4 mauvais headlines vs 4 bons (utilisant prénom voc + service voc + délai voc + zone profil)
- Section 4 Services : 3 mauvaises descriptions vs 3 bonnes (extraites des avis réels reformulés)
- Section 7 Zone : règle "villes citées dans reviews_all.json en priorité, ne pas inventer un rayon"

Insight : les LLMs calibrent bien sur des exemples concrets. Les règles abstraites sans exemples produisent du mode 2 générique.

### 5. Seuils de mentions explicites en §5bis
Bug : mention unique d'un fait dans 142 avis (ex: "garantie 2 ans" cité par 1 client) → Claude ne savait pas si c'était à affirmer ou pas.

Solution : tableau explicite des seuils :
| Nb mentions | Statut | Action |
|---|---|---|
| 0 | ABSENT | Mode 2 ou suppression |
| 1 | EXCEPTION | Mode 2 + citation littérale attribuée si vraiment marquant |
| 2-3 | PROBABLE | Mode 1 sans citation |
| ≥ 4 | VÉRIFIÉ | Mode 1 affirmé |

### 6. audit_content.py : detection regex post-build avec normalisation accents
Script généré dans workspace qui scanne `site/*.html` pour patterns suspects (dates, prix, durées, noms propres, garanties). Confronte aux sources brutes (profil.json, reviews_all.json, reviews_meta.json). Sort `HALLUCINATIONS_REPORT.md` avec [OK]/[?]/[X].

Bug fixé : `fact_in_source()` faisait `.lower()` mais pas de normalisation Unicode. "Méhdi" (avec accent dans reviews) vs "Mehdi" (sans) → faux positif. Fix : `unicodedata.normalize("NFKD")` + encode ASCII ignore. Plus de faux positifs sur les noms réels.

### 7. preview_site.py : scroll avant screenshot
Bug initial : Playwright screenshote sans scroll → IntersectionObservers pas déclenchés → toutes les sections sous le fold sont blanches. Solution 4 lignes : scrollTo bottom → scrollTo top → wait 0.4s → screenshot. Plus besoin de fallback setTimeout dans le HTML.

### 8. reviews_inline.js au lieu de fetch('reviews.json')
Bug : composants Alpine.js faisaient `fetch('reviews.json')` async. En file:// (CORS) ou lors du screenshot Playwright (race condition), contenu vide.

Solution : générer `reviews_inline.js` qui set `window.SITE_REVIEWS = [...]` et `window.SITE_REVIEWS_META = {...}` synchrone. Composants utilisent `window.SITE_REVIEWS` directement. Met aussi à jour `.js-review-count` / `.js-review-rating` / `a.js-review-url` au DOMContentLoaded.

### 9. Structure site/ séparée du workspace
Refactor : tous les fichiers HTML + assets déployables vont dans `out_dir/site/`. Workspace (CLAUDE.md, COMPONENTS.md, PRODUCT.md, reviews_all.json, profil.json, preview_site.py, audit_content.py, finalize_site.py, POST_DEPLOY.md) reste à la racine. Deploy : `cd site && vercel --prod`. Pas besoin de `.vercelignore`.

## What failed

### voc.json (pré-extraction par regex)
Tenté puis retiré. 200 lignes de code (`_FRENCH_FIRST_NAMES` liste de 300 prénoms, `_QUALITY_ADJECTIVES` 50 adjectifs, `_SERVICE_KEYWORDS` par métier, 5 helpers `_extract_*`). Problèmes :
- Listes incomplètes (prénoms étrangers ratés, accents variables)
- Claude devient paresseux : il fait confiance à voc.json incomplet et ne lit plus reviews_all.json
- Dette technique permanente (maintenir les listes)
- Insulte l'intelligence de Claude

L'utilisateur a tranché explicitement : "C'est à Claude d'interpréter, c'est lui le cerveau. Le script collecte, point."

### societe.com + DuckDuckGo (enrichissement legal/social)
Tentative d'auto-fetch SIRET via societe.com et réseaux sociaux via DDG HTML search. Résultats en production :
- societe.com → Cloudflare bloque 80% du temps
- DDG → rate-limit après quelques requêtes en batch
- Quand ça marche : risque de matcher la mauvaise entreprise homonyme

Retiré complètement. Remplacé par placeholders manuels (`SIRET : [À compléter]` dans mentions légales). 1 ligne dans POST_DEPLOY.md pour rappeler.

### Pré-deploy check standalone
Initialement prévu `pre_deploy_check.py` qui scannait les placeholders avant deploy. Rendu redondant par `finalize_site.py` qui garantit le replace par construction.

### 50 gaps identifiés en simulation
Première simulation profonde du workflow → 50 "gaps" listés. Sur tri honnête : 5 vrais critiques + 4 trous de doc + 41 sur-engineering paranoïaque. Pattern : tentation d'engineerer autour de la "potentielle paresse" de Claude au lieu de lui faire confiance.

## Key pattern

**Le script Python collecte et organise. Claude analyse et interprète. Pas de mélange.**

Tout le reste découle de ce principe :
- Pas de voc.json (Claude lit les avis lui-même)
- Pas de manifest photo auto (Claude regarde et décide)
- Audit post-build via recherche substring brute (pas d'extraction sémantique)
- §7 donne des exemples concrets de bon contenu, pas des règles abstraites
- Quand la donnée manque : Mode 2 générique honnête, OU suppression de section, JAMAIS invention plausible

**Side principle** : pour les workflows multi-étapes avec valeurs inconnues à l'étape N mais connues à N+k (URL Vercel, email client), utiliser des **marqueurs uniques** + script de **finalisation ré-exécutable avec state file**. Pas de placeholders ambigus type `VOTRE-SITE.vercel.app` qui ressemblent à du vrai contenu.

## Saves next time

- **Ne pas faire de pré-extraction "sémantique" en Python** quand Claude peut faire mieux. Listes de mots-clés = piège.
- **Toujours considérer la ré-exécutabilité** d'un script de finalisation. Si on injecte des valeurs, on doit pouvoir les changer plus tard sans tout casser. State file = standard pattern.
- **Marqueurs `__XXX__` > placeholders type "VOTRE-SITE"** : si un marqueur survit au déploiement, il est immédiatement visible (alors qu'un placeholder ressemble à du vrai texte).
- **§7 SPECS structurelles seules = contenu générique**. Ajouter des exemples Bon/Mauvais concrets pour calibrer le LLM sur la qualité de copy attendue.
- **Pour anti-hallucination LLM**, deux mécanismes complémentaires : (1) protocole de rédaction explicite avec modes autorisés/interdits dans le brief, (2) audit regex post-build qui flag les chiffres/noms non trouvés dans les sources. Pas besoin d'un second LLM.
- **Quand on tombe sur 30+ "gaps potentiels"**, faire un tri honnête : critique (casse en prod) vs minor (UX) vs sur-engineering (béquille pour Claude). Souvent 80% sont du dernier groupe.
- **Workflow démo→vente pour sites artisans** : URL Vercel auto comme intermédiaire est OK pour la phase démo. Custom domain configuré après la vente. `finalize_site.py` géré les 2 transitions.
- **FormSubmit.co activation par email destinataire** : si on change l'email après une 1ère activation, il faut RE-ACTIVER (la combinaison form/email change = FormSubmit considère nouveau form). Documenter explicitement dans POST_DEPLOY.
- **CLAUDE.md de 1500 lignes** : OK si bien structuré (sections numérotées, gates explicites). Le risque "lost in the middle" est moindre que la perte d'information si on fragmente trop.
- **Skills Claude Code (`$impeccable`, `$emil-design-eng`)** : peuvent être absents selon la config user. Toujours mentionner un fallback manuel dans CLAUDE.md.

## Fichiers clés du workspace après scraper

```
profils/{nom_artisan}/
├── CLAUDE.md            (1527 lignes — conducteur de build)
├── COMPONENTS.md        (7 composants vanilla : NumberTicker, TextHighlighter, ImageGallery,
│                         BeforeAfterSlider, NavigationMenuDesktop, TestimonialsColumns, SimpleReviewCarousel)
├── PRODUCT.md           (contexte métier pour skill impeccable)
├── POST_DEPLOY.md       (workflow démo → vente → custom domain → FormSubmit activation)
├── reviews_all.json     (TOUS les avis bruts — Claude les lit lui-même)
├── profil.json/.txt     (données brutes Google Places)
├── preview_site.py      (capture screenshots avec scroll preliminary)
├── audit_content.py     (audit anti-hallucination regex)
├── finalize_site.py     (remplace __SITE_URL__ + __CLIENT_EMAIL__, ré-exécutable via .finalize_state.json)
└── site/                ← DEPLOYABLE (cd site && vercel --prod)
    ├── favicon.svg, sitemap.xml, robots.txt   (marqueurs __SITE_URL__)
    ├── reviews.json, reviews_meta.json, reviews_inline.js
    ├── photos/  (jpg + webp variants)
    └── videos/
```
