---
date: 2026-05-27
topic: pipeline-construction-phase-features
project: web-design-artisans
compiled: false
---

## Context

Suite de la refonte du pipeline `company_scraper_bis.py` (artisan website builder). Session focalisée sur la **phase de construction par Claude** — comment Claude utilise réellement les ressources du workspace pour bâtir le site. Travail en 4 grandes phases :

1. **Simulation profonde de la construction** (Act 0-V avec H2O Plomberie) → identification de 9 gaps, dont 4 réels stratégiques
2. **Développement de 6 features amélioratives** → 4 intégrées sur décision user
3. **Évaluation de 2 analyses externes** (AI-generated) prétendant identifier des bugs → tri honnête entre bugs réels et sophistication
4. **Fix de 3 vrais bugs** confirmés par vérification code

## What worked

### 1. Méthode de simulation de la construction Claude (étape par étape)

Au lieu de tester abstraitement, on a simulé pas à pas ce que Claude fait avec un cas concret (H2O Plomberie, 156 avis, 18 photos, services plomberie). Format ACT 0 → ACT V :
- ACT 0 : setup et ouverture CLAUDE.md
- ACT I : PHASE 1 — analyse photos + reviews + décisions design
- ACT II : PHASE 2 — architecture fichiers
- ACT III : PHASE 3 — build HTML section par section

Cette méthode a révélé 9 gaps que les tests automatiques n'auraient jamais détectés. Les 4 vrais (sur 9) :
- DÉCISION COMPOSANTS demandée AVANT analyse photos
- CONCEPT DESIGN sans avoir vu les photos
- §7 SPECS structurelles ≠ stratégiques (Claude génère du contenu générique)
- Mention unique d'un fait dans 142 avis : comportement non spécifié

### 2. §7 SPECS PAR PAGE : transformation structurelle → stratégique

C'est le gain de valeur le plus important. Avant, §7 disait *"Section Services = 3 cards icône+titre+description"* (structurel). Claude écrivait des descriptions génériques creuses ("Notre équipe d'experts à votre service").

Après refonte, §7 inclut des **exemples Bon/Mauvais concrets** :
- Hero : 4 mauvais headlines vs 4 bons (utilisant prénom voc + service voc + délai voc + zone profil)
- Services : 3 mauvaises descriptions vs 3 bonnes (extraites des avis réels reformulés)
- Zone : règle "villes citées dans reviews_all.json en priorité"

Insight : les LLMs calibrent bien sur exemples concrets. Règles abstraites seules produisent du Mode 2 générique.

### 3. PHASE 1 réordonnée : matériau AVANT décisions design

Bug initial : §6 PHASE 1 demandait CONCEPT DESIGN + DÉCISION COMPOSANTS avant l'analyse photos. Donc Claude faisait des choix au pifomètre puis devait rétro-ajuster.

Fix : restructurer en 5 étapes :
```
1.1 Inventaire ressources (ls + lire COMPONENTS.md)
1.2 Analyser photos (§4 gate) + manifest.json
1.3 Lire reviews_all.json (§5bis)
1.4 Décisions design (§4ter — maintenant avec le matériau)
1.5 Décisions composants (maintenant avec toutes les données)
```

### 4. Port React/Radix UI → vanilla HTML (FAQAccordion)

L'utilisateur a fourni un Accordion Radix React/TypeScript pour la FAQ. Notre stack étant vanilla (HTML + Alpine.js + Tailwind CDN), j'ai porté en :
- `<details name="faq-X">` natif HTML5 (Chrome 120+, Firefox 134+, Safari 17.2+ → universel 2026)
- Animation slide-down via `grid-template-rows: 0fr → 1fr` (CSS moderne, pas de JS)
- Chevron rotation via `details[open] .accordion-chevron { transform: rotate(180deg) }`
- Exclusivité single-open via attribute `name="faq-X"` (équivalent Radix `type="single"`)

**Fidélité visuelle 100%, fidélité comportementale 100%, bonus : 0 JS et indexable Google**.

### 5. Méthode FAQ : extraction depuis avis, jamais inventée

Insight clé : les questions ne s'inventent pas, elles s'extraient des avis. Quand un client écrit *"il est arrivé le 14 juillet à 6h30"*, il répond à *"Intervenez-vous les jours fériés ?"*.

Méthode documentée dans §7 Section 7 :
1. Lire reviews_all.json en cherchant peurs implicites + éloges récurrents
2. Regrouper par catégorie d'objection : Réactivité / Méthode / Tarif / Zone
3. 5-8 questions max, chaque réponse Mode 1 sourcé OU Mode 2 générique + **micro-CTA naturel en fin**

Conditional : INCLURE FAQ uniquement si reviews_all.json contient ≥ 30 avis (matière suffisante).

### 6. Micro-copy table comparative — 17 zones template vs premium

§7bis nouvelle section. Pour chaque zone du site (bouton CTA, placeholder form, label, alt text, message merci, aria-label, etc.) :
- ❌ Template : "Envoyer ma demande"
- ✅ Premium : "Demander mon devis gratuit →"

Pattern : remplacer générique par spécifique (prénom artisan, ville sourcée, durée sourcée, exemple concret). Règle : prénom JAMAIS inventé si absent de reviews_all.json.

### 7. Transitions fonds entre sections — 3 approches dosées

§4ter Bloc 4 ajouté (Cinématographie du scroll). 3 approches :
- **Variations subtiles** (approche par défaut) : nuances 3-5% luminance entre sections claires + 1 sombre ponctuelle pour impact maximal
- **Overlap de contenu** : élément qui chevauche 2 sections (ex: badge note Google entre hero et trust bar)
- **SVG wave/angle** : uniquement si forme matche la géométrie du design (anti-pattern WordPress 2018 sinon)

Règles : max 4 nuances claires + 1 sombre par page.

### 8. Animations premium dosées (StaggerReveal + CursorAwareShadow)

2 composants utilitaires ajoutés à COMPONENTS.md :
- **StaggerReveal** : apparition séquencée des enfants d'une section (délai 80ms inter-élément). MAX 2 sections par page. Fallback `setTimeout(2000)` pour Playwright headless.
- **CursorAwareShadow** : ombre qui suit le curseur sur Service cards homepage. Désactivé sur touch et reduced-motion. Amplitude max 16px.

User a explicitement REFUSÉ ServiceCardHover (chorégraphie hover) pour rester sobre.

### 9. Fix #2 critique : split-shift hours (re.findall + JS widget arrays)

Bug réel : `re.search` capturait seulement le 1er intervalle. Pour "08:00 – 12:00, 14:00 – 18:00" (pause méridienne très commune chez artisans FR), widget JS disait "Fermé" toute l'après-midi.

Fix double :
- Python : `re.findall` pour capturer N intervalles par jour
- JS : `BUSINESS_HOURS[day]` devient un **array** d'intervalles. `getBusinessStatus()` itère sur l'array pour trouver le bon intervalle. `getNextOpen()` utilise `intervals[0].open`.

Simulation runtime validée sur 9 heures du jour : 4 heures de mensonge factuel éliminées (entre 14h et 18h).

### 10. Fix #3 trivial : profile_photo_url retenu dans merge_reviews

1 ligne ajoutée. Les ~5 avis API gardent maintenant leur vraie photo de profil Google au lieu de fallback générique ui-avatars.com.

### 11. Fix #6 cosmétique : duplications POINT D'ENTREE (3 → 1)

10 lignes mortes nettoyées.

## What failed

### Bugs introduits par moi-même lors de l'ajout des features (3 bugs)

Après intégration des 3 nouveaux composants (FAQ, Stagger, CursorShadow), j'ai oublié :
1. Le bloc DÉCISION COMPOSANTS ne listait que les 7 anciens composants
2. PHASE 1 Étape 1.1 disait "inventaire des 7 composants" (7 au lieu de 10)
3. Le Bloc 4 Cinématographie ajouté à §4ter n'était pas listé dans PHASE 1 Étape 1.4

Détection : audit manuel après tests automatiques (les checks regex avaient passé mais ne couvraient pas ces incohérences inter-sections).

### Analyse externe #1 : 2/6 claims étaient faux

L'analyse AI-generated #1 (6 "critical flaws") contenait :
- Claim #1 (URL photos Maps) : le "bug" et le "fix" proposés étaient **littéralement identiques** → auto-contradictoire, probable hallucination
- Claim #5 (lazy images false positives) : le check JS `i.complete && i.naturalWidth === 0` est techniquement correct (lazy non-loaded → complete=false → pas flagged). Analyse technique fausse.

### Analyse externe #2 : 5/5 claims rejetés en bloc

L'analyse AI-generated #2 ("Context Window Degradation", "Agentic Sequencing", "Cognitive Overload", "Component Injection Fragility", "Contradictory Design Directives") :
- 2 théoriquement valides mais fixes changent la nature du produit (one-shot → phase-by-phase, no-build-step → build step)
- 1 = exactement la voc.json déjà rejetée explicitement
- 1 basé sur mauvaise lecture du document (§4ter offre options, ne dicte pas)
- 1 = théorie "lost in middle" appliquée maladroitement

Aucun n'a justifié de modification.

### Tentation d'accepter la sophistication

Le pattern récurrent dangereux : une analyse AI-générée écrite avec autorité ingénieur, qui sonne crédible mais contient un mix d'observations valides et de claims qui contredisent des décisions architecturales explicites. Si on accepte toutes, on retombe dans l'over-engineering (voc.json, build step, phase-by-phase, etc.) qu'on a délibérément rejeté.

## Key pattern

**Pour évaluer une analyse externe d'un workflow LLM, deux étapes obligatoires** :

1. **Vérifier chaque claim contre le code réel** — beaucoup de claims AI-generated sonnent crédibles mais ne tiennent pas au test (URL identique entre bug et fix, lazy images logique mal comprise, etc.)

2. **Vérifier chaque claim contre les décisions architecturales explicitement actées** — si un claim propose un "fix" qui revient sur une décision documentée (voc.json supprimée, no-build-step, one-shot prompt), c'est probablement une suggestion à l'opposé de la philosophie du produit, pas un bug.

Pattern complémentaire : **§7 SPECS d'un workflow LLM doivent être structurelles + stratégiques**. Structurelles seules → contenu générique. Stratégiques = exemples Bon/Mauvais concrets qui calibrent le LLM sur la qualité de copy attendue.

Pattern complémentaire 2 : **Le port React/Radix → vanilla est presque toujours faisable** avec `<details>/<summary>` natif HTML5 + `grid-template-rows: 0fr → 1fr` pour les accordéons, attribute `name="X"` pour single-open, `details[open]` pour le state CSS. Zero JS pour le mécanisme.

## Saves next time

- **Ne pas accepter une analyse AI-generated sans vérification triple** : (1) code réel, (2) décisions actées, (3) simulation runtime.
- **Quand on ajoute un composant à COMPONENTS.md, vérifier en parallèle** : (a) DÉCISION COMPOSANTS bloc l'inclut-il ? (b) §14 récap mentionne-t-il ? (c) Compteurs "N composants" mis à jour ? Ces 3 cohérences inter-sections sont des bugs typiques d'ajout.
- **Pour les LLM, donner des exemples concrets > donner des règles abstraites**. Une règle "écris un bon headline" produit du générique. Une table 4 mauvais × 4 bons exemples calibre instantanément le modèle.
- **Split-shift hours est un cas réel pour les artisans FR** (pause méridienne très commune). Toujours supporter via array d'intervalles `BUSINESS_HOURS[day] = [{open,close}, ...]` plutôt que `{open,close}` simple. La regex `re.search` rate les multiples, utiliser `re.findall`.
- **Pour les pages contenant une FAQ visible**, le schema JSON-LD `FAQPage` est obligatoire pour le SEO. Cohérence textuelle schema ↔ HTML vérifiée par Google. Si modif d'une question dans le HTML, modifier aussi dans le schema.
- **Port Radix accordion en vanilla** : `<details name="faq">` (single-open natif HTML5) + grid-template-rows pour animation height auto + `details[open] .chevron { transform: rotate(180deg) }`. Pattern réutilisable pour tout accordion de doc/FAQ.
- **Fonctionnellement, l'analyse "Lost in the Middle"** est exagérée sur Claude 4+. Triple maillage mindset top + anti-hallu milieu + never do bas = mieux qu'un re-ordonnancement qui dilue la narration.
- **Le ServiceCardHover (hover chorégraphié à 4 propriétés)** était proposé mais user a refusé pour rester sobre. Lesson : ne pas empiler les effets visuels "parce qu'ils sont cool" — chaque animation premium doit gagner sa place.
- **`finalize_site.py` doit toujours être ré-exécutable** via state file `.finalize_state.json` pour gérer les 2 transitions (démo URL Vercel → custom domain custom).
- **Quand un user demande "y a-t-il des bugs ?"**, faire un audit honnête section par section au lieu de répondre "tout marche". Les bugs d'incohérence inter-sections sont les plus faciles à rater aux tests automatiques.

## Métriques finales après cette session

- `company_scraper_bis.py` : ~4800 lignes (vs ~4373 au début de session)
- CLAUDE.md généré : **1812 lignes** / 86 KB / ~21K tokens input
- COMPONENTS.md : **907 lignes**, **10 composants**
- 3 vrais bugs fixés (split-shift, profile_photo_url, POINT D'ENTREE)
- 2 analyses externes évaluées → 1 vrai bug + 1 cosmétique gardés sur 11 claims totaux
- 4 features stratégiques intégrées (FAQ, micro-copy, transitions fonds, animations premium dosées)
