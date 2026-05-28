"""
Company Profile Scraper — Version 3
Extrait TOUTES les données Google d'un prospect et génère l'intégralité
des fichiers nécessaires au pipeline Claude Code Direct (sans Stitch).

SOURCES :
  - Google Places API  → données structurées (adresse, note, horaires, types, 10 photos)
  - Google Maps scrape → jusqu'à ~40 avis clients dédupliqués

OUTPUT par entreprise (dossier profils/NOM_ENTREPRISE/) :
  ├── CLAUDE.md           → conducteur de build complet (lu automatiquement par Claude Code)
  ├── PRODUCT.md          → contexte métier pour le skill impeccable
  ├── reviews.json        → avis format frontend (name, role, rating, text, image)
  ├── profil.json         → données brutes API (référence)
  ├── profil.txt          → fiche lisible (référence)
  ├── photos/
  │   ├── photo_01.jpg    → API Google Places (auto)
  │   ├── ...
  │   └── photo_XX.jpg    → photos ajoutées manuellement (facultatif)
  └── videos/             → présent si vidéos détectées sur Maps ou ajoutées manuellement
      ├── video_01.mp4    → Google Maps scrape (auto)
      └── video_XX.mp4    → vidéos ajoutées manuellement (facultatif)

WORKFLOW :
  1. python3 company_scraper_bis.py --place_id <ID>
  2. Ouvre Claude Code → workspace = profils/NOM_ENTREPRISE/
  3. Claude lit CLAUDE.md automatiquement
  4. Prompt : "Build the site."  → site complet en une passe
  5. /deploy → Vercel

USAGE :
  python3 company_scraper_bis.py --place_id ChIJxRiWGsjl5EcRjf_0YrmRyIM
  python3 company_scraper_bis.py --nom "SAS GAMARY" --ville "Orléans"
  python3 company_scraper_bis.py --csv prospects.csv --priorite CHAUD
  export GOOGLE_PLACES_API_KEY=ta_cle && python3 company_scraper_bis.py --csv prospects.csv --priorite CHAUD
"""

import os, re, sys, csv, json, time, argparse, requests
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus


# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────

DETAILS_URL    = "https://maps.googleapis.com/maps/api/place/details/json"
SEARCH_URL     = "https://maps.googleapis.com/maps/api/place/textsearch/json"
PHOTO_URL      = "https://maps.googleapis.com/maps/api/place/photo"
OUTPUT_DIR     = Path("profils")
PHOTO_MAXWIDTH = 1600
MAX_REVIEWS_DISPLAY = 12     # avis sélectionnés pour reviews.json (frontend)
MAX_REVIEWS_TOTAL   = 500    # plafond hard du scrape (sécurité)
MAX_PHOTOS_API      = 10

ALL_FIELDS = ",".join([
    "name", "formatted_address", "formatted_phone_number",
    "international_phone_number", "website", "url", "rating",
    "user_ratings_total", "reviews", "photos", "opening_hours",
    "business_status", "types", "editorial_summary",
    "geometry", "place_id",
])


# ─────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────

def safe_dirname(name: str) -> str:
    return re.sub(r"[^\w\s-]", "", name).strip().replace(" ", "_")[:60]

def stars(r: float) -> str:
    r = float(r or 0)
    return "★" * int(r) + "☆" * (5 - int(r)) + f" ({r}/5)"

def clean_text(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()


# ─────────────────────────────────────────────────────────────
# GOOGLE PLACES API
# ─────────────────────────────────────────────────────────────

def api_search(nom: str, ville: str, api_key: str):
    params = {"query": f"{nom} {ville}", "language": "fr", "region": "fr", "key": api_key}
    data = requests.get(SEARCH_URL, params=params, timeout=10).json()
    if data.get("status") != "OK" or not data.get("results"):
        print(f"  Introuvable : {nom} {ville}")
        return None
    r = data["results"][0]
    print(f"  Trouve : {r.get('name')} — {r.get('formatted_address','')[:60]}")
    return r["place_id"]


def api_details(place_id: str, api_key: str):
    params = {
        "place_id": place_id, "fields": ALL_FIELDS,
        "language": "fr", "reviews_no_translations": "true", "key": api_key,
    }
    data = requests.get(DETAILS_URL, params=params, timeout=15).json()
    if data.get("status") == "REQUEST_DENIED":
        print(f"  Cle API refusee : {data.get('error_message','')}")
        sys.exit(1)
    if data.get("status") != "OK":
        print(f"  API status : {data.get('status')}")
        return None
    return data.get("result", {})


def api_download_photos(photo_refs: list, dest: Path, api_key: str) -> list:
    dest.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, ref_obj in enumerate(photo_refs[:MAX_PHOTOS_API], 1):
        ref = ref_obj.get("photo_reference", "")
        if not ref:
            continue
        try:
            resp = requests.get(
                PHOTO_URL,
                params={"maxwidth": PHOTO_MAXWIDTH, "photo_reference": ref, "key": api_key},
                timeout=20, stream=True
            )
            ct  = resp.headers.get("Content-Type", "image/jpeg")
            ext = "jpg" if "jpeg" in ct else ct.split("/")[-1]
            fp  = dest / f"photo_{i:02d}.{ext}"
            with open(fp, "wb") as f:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
            size = fp.stat().st_size // 1024
            print(f"    [API] photo_{i:02d}.{ext}  {size} KB")
            paths.append(str(fp))
        except Exception as e:
            print(f"    photo {i} erreur : {e}")
        time.sleep(0.2)
    return paths


# ─────────────────────────────────────────────────────────────
# GOOGLE MAPS SCRAPING
# ─────────────────────────────────────────────────────────────

def _click_reviews_tab(page) -> bool:
    """
    Clique sur l'onglet 'Avis' de Google Maps.
    Essaie plusieurs stratégies dans l'ordre :
      1. page.click() avec aria-label CSS (lève une exception si absent → try/except)
      2. Parcours de tous les [role=tab] et inspection du texte visible
      3. Playwright locator get_by_role
    Retourne True si le tab a été cliqué.
    """
    # Stratégie 1 : CSS aria-label (le plus rapide)
    try:
        page.click('button[aria-label*="Avis"]', timeout=4000)
        time.sleep(2)
        print("    Tab Avis : cliqué via aria-label")
        return True
    except Exception:
        pass

    # Stratégie 2 : parcourir tous les role=tab et vérifier le texte
    try:
        tabs = page.query_selector_all('[role="tab"]')
        for tab in tabs:
            label = (tab.get_attribute("aria-label") or "").lower()
            inner = ""
            try:
                inner = tab.inner_text().lower()
            except Exception:
                pass
            if "avis" in label or "avis" in inner or "review" in label:
                tab.click()
                time.sleep(2)
                print(f"    Tab Avis : cliqué via scan des tabs (label='{label[:40]}')")
                return True
    except Exception:
        pass

    # Stratégie 3 : Playwright locator (API native, supporte :has-text)
    try:
        loc = page.locator('[role="tab"]').filter(has_text="Avis")
        if loc.count() > 0:
            loc.first.click(timeout=3000)
            time.sleep(2)
            print("    Tab Avis : cliqué via locator filter")
            return True
    except Exception:
        pass

    return False


def scrape_maps(maps_url: str, dest_photos: Path, existing_count: int):
    """
    Scrape Google Maps pour extraire les avis clients (~40 avis).
    Photos : uniquement via API (10 photos haute qualite — voir api_download_photos).
    Pour ajouter des photos supplementaires, les deposer manuellement dans photos/.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  Playwright non disponible — installer avec : pip install playwright && playwright install chromium")
        return [], []

    reviews = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            locale="fr-FR",
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = ctx.new_page()

        # ── Charger la page Maps ──────────────────────────────
        # "load" (pas domcontentloaded) pour que le JS Maps ait rendu les tabs
        try:
            page.goto(maps_url, wait_until="load", timeout=35000)
            time.sleep(2.5)
            # Rejeter cookies si présents
            for sel in [
                'button[aria-label*="Refuser"]',
                'button[aria-label*="Reject"]',
                'button[aria-label*="Tout refuser"]',
                'button[jsname="tWT92d"]',
            ]:
                btn = page.query_selector(sel)
                if btn:
                    btn.click()
                    time.sleep(1)
                    break
        except Exception as e:
            print(f"  Chargement Maps : {e}")
            browser.close()
            return [], []

        # ─────────────────────────────────────────────────────
        # AVIS — onglet Avis + scroll container
        # ─────────────────────────────────────────────────────
        try:
            tab_ok = _click_reviews_tab(page)

            if not tab_ok:
                print("    Onglet avis inaccessible — 0 avis Maps")
            else:
                # Trier par "Les plus récents" pour avoir de la diversité
                for sort_sel in [
                    'button[aria-label*="Trier"]',
                    'button[aria-label*="Sort"]',
                    'button[data-value*="sort"]',
                ]:
                    btn = page.query_selector(sort_sel)
                    if btn:
                        btn.click()
                        time.sleep(1)
                        # Choisir option 2 (Plus récents) via scan
                        opts = page.query_selector_all('[role="menuitemradio"], [role="option"]')
                        if len(opts) >= 2:
                            opts[1].click()
                            time.sleep(2)
                        break

                # ── Scroll dans le panneau latéral ───────────────────────
                # .m6QErb.DxyBCb est le div scrollable (confirmé par test B)
                SCROLL_SELECTORS = [
                    ".m6QErb.DxyBCb",
                    ".m6QErb[tabindex]",
                    'div[role="feed"]',
                ]
                scroll_el = None
                for sel in SCROLL_SELECTORS:
                    scroll_el = page.query_selector(sel)
                    if scroll_el:
                        print(f"    Conteneur scroll trouvé : {sel}")
                        break

                if scroll_el:
                    # Scroll DYNAMIQUE — continuer tant que de nouveaux avis apparaissent
                    # Stop si pas de gain pendant `stable_max` itérations consécutives
                    seen_count   = 0
                    stable_iters = 0
                    stable_max   = 4    # 4 scrolls sans gain → fin
                    max_iters    = 200  # plafond sécurité (~200 * 4 = 800 avis théoriques)
                    for i in range(max_iters):
                        page.evaluate("el => el.scrollTop += 1200", scroll_el)
                        time.sleep(0.5)
                        # Déployer les textes tronqués progressivement
                        for expand_btn in page.query_selector_all(".w8nwRe, button.M77dve")[:30]:
                            try:
                                expand_btn.click()
                                time.sleep(0.04)
                            except Exception:
                                pass
                        # Compter les blocs avis actuellement chargés
                        current = len(page.query_selector_all('div[data-review-id]'))
                        if current >= MAX_REVIEWS_TOTAL:
                            print(f"    Plafond atteint ({MAX_REVIEWS_TOTAL} avis) — arrêt")
                            break
                        if current == seen_count:
                            stable_iters += 1
                            if stable_iters >= stable_max:
                                print(f"    Fin du scroll dynamique : {current} avis chargés ({i+1} scrolls)")
                                break
                        else:
                            stable_iters = 0
                            seen_count   = current
                else:
                    print("    Conteneur scroll non trouvé — fallback page scroll")
                    for _ in range(20):
                        page.keyboard.press("End")
                        time.sleep(0.8)

                # ── Parser le HTML ────────────────────────────────────────
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(page.content(), "lxml")

                # data-review-id est l'attribut le plus stable
                review_blocks = soup.find_all("div", attrs={"data-review-id": True})
                if not review_blocks:
                    review_blocks = soup.find_all("div", class_=re.compile(r"\bjftiEf\b"))

                for block in review_blocks:
                    # Auteur
                    author_el = (
                        block.find(class_=re.compile(r"\bd4r55\b"))
                        or block.find("button", class_=re.compile(r"\bal6Kxe\b"))
                        or block.find(class_=re.compile(r"reviewer", re.I))
                    )
                    author = clean_text(author_el.get_text()) if author_el else "Client"

                    # Note (aria-label "X étoile(s)" sur un span)
                    note = 0
                    note_el = block.find(attrs={"aria-label": re.compile(r"étoile|star", re.I)})
                    if note_el:
                        m = re.search(r"(\d)", note_el.get("aria-label", ""))
                        if m:
                            note = int(m.group(1))

                    # Texte de l'avis (span.wiI7pd contient le texte complet après "Voir plus")
                    text_el = block.find(class_=re.compile(r"\bwiI7pd\b"))
                    text = ""
                    if text_el:
                        text = clean_text(text_el.get_text())
                        text = re.sub(r"\b(Plus|Moins|More|Less|Voir plus|Voir moins)\b", "", text).strip()

                    # Date relative ("il y a 3 semaines", etc.)
                    date_el = block.find(class_=re.compile(r"\brsqaWe\b"))
                    date_str = clean_text(date_el.get_text()) if date_el else ""

                    if author and (text or note > 0):
                        reviews.append({
                            "author": author,
                            "rating": note,
                            "text":   text,
                            "date":   date_str,
                        })

                print(f"    {len(reviews)} avis extraits via Maps (avant déduplication)")

        except Exception as e:
            print(f"    Avis Maps erreur : {e}")

        # ─────────────────────────────────────────────────────
        # PHOTOS SUPPLÉMENTAIRES — onglet "Photos" sur la page Maps
        # ─────────────────────────────────────────────────────
        new_photos = _scrape_maps_photos(page, dest_photos, start_idx=existing_count + 1)

        browser.close()

    return new_photos, reviews


def _scrape_maps_photos(page, dest: Path, start_idx: int, max_n: int = 20) -> list:
    """
    Scrape l'onglet Photos de la page Maps (déjà chargée).
    Récupère jusqu'à max_n photos supplémentaires, nommées photo_{start_idx:02d}.jpg, etc.
    Retourne la liste des chemins téléchargés.
    """
    downloaded = []

    # 1. Cliquer sur l'onglet Photos
    photos_clicked = False
    for sel in [
        'button[aria-label*="Photos"][role="tab"]',
        'button[aria-label*="Photo"][role="tab"]',
        'button[jsaction*="pane.heroHeaderImage"]',
    ]:
        try:
            btn = page.query_selector(sel)
            if btn:
                btn.click()
                time.sleep(2)
                photos_clicked = True
                print(f"    Onglet Photos : cliqué via {sel[:50]}")
                break
        except Exception:
            continue

    if not photos_clicked:
        # Fallback : essayer via locator avec texte
        try:
            loc = page.locator('button[role="tab"]').filter(has_text=re.compile(r"^Photos?$|^Photo$", re.I))
            if loc.count() > 0:
                loc.first.click(timeout=3000)
                time.sleep(2)
                photos_clicked = True
                print("    Onglet Photos : cliqué via locator")
        except Exception:
            pass

    if not photos_clicked:
        print("    Onglet Photos inaccessible — pas de photos supplémentaires")
        return []

    # 2. Scroller pour charger toutes les vignettes
    try:
        # Le conteneur des photos a une classe variable, on tente plusieurs sélecteurs
        scroll_el = None
        for sel in [".m6QErb.DxyBCb", ".m6QErb[tabindex]", 'div[role="main"]']:
            scroll_el = page.query_selector(sel)
            if scroll_el:
                break

        if scroll_el:
            seen, stable = 0, 0
            for _ in range(40):
                page.evaluate("el => el.scrollTop += 1500", scroll_el)
                time.sleep(0.5)
                # Compter les vignettes chargées
                current = len(page.query_selector_all('a[data-photo-index], div[data-photo-index]'))
                if current == 0:
                    # Fallback : compter les <img> qui pointent vers lh3
                    imgs = page.query_selector_all('img[src*="lh3."]')
                    current = len(imgs)
                if current >= max_n + 20:  # marge pour filtrage
                    break
                if current == seen:
                    stable += 1
                    if stable >= 3:
                        break
                else:
                    stable, seen = 0, current
    except Exception as e:
        print(f"    Scroll photos erreur : {e}")

    # 3. Extraire les IDs depuis le HTML
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(page.content(), "lxml")

        ids = []
        seen_ids = set()
        # Chercher dans img src, srcset, et style background-image
        for el in soup.find_all(["img", "div", "a"]):
            srcs = []
            if el.get("src"):    srcs.append(el["src"])
            if el.get("srcset"): srcs.append(el["srcset"])
            style = el.get("style", "")
            if "background-image" in style:
                srcs.append(style)

            for src in srcs:
                # Pattern lh3.googleusercontent.com/p/{ID} ou lh3.ggpht.com/p/{ID}
                m = re.search(r"lh3\.(?:googleusercontent|ggpht)\.com/(?:p/)?([A-Za-z0-9_-]{20,})", src)
                if m:
                    pid = m.group(1)
                    # Ignorer les avatars (URLs avec "a/ACg8oc..." ou similaires)
                    if pid.startswith("ACg8oc") or pid.startswith("a-"):
                        continue
                    if pid not in seen_ids:
                        seen_ids.add(pid)
                        ids.append(pid)

        print(f"    {len(ids)} IDs photo extraits de l'onglet Photos")

        # 4. Télécharger les premiers max_n (les plus en haut de la grille = les plus populaires)
        for i, pid in enumerate(ids[:max_n]):
            try:
                url = f"https://lh3.googleusercontent.com/p/{pid}=s1600"
                resp = requests.get(url, timeout=15, stream=True)
                if resp.status_code != 200:
                    continue
                fp = dest / f"photo_{start_idx + i:02d}.jpg"
                with open(fp, "wb") as f:
                    for chunk in resp.iter_content(8192):
                        f.write(chunk)
                size = fp.stat().st_size // 1024
                if size < 5:  # photo trop petite (probablement vide/erreur)
                    fp.unlink()
                    continue
                downloaded.append(str(fp))
                print(f"    [MAPS] photo_{start_idx + i:02d}.jpg  {size} KB")
                time.sleep(0.15)
            except Exception as e:
                print(f"    photo {pid[:12]} erreur : {e}")

    except Exception as e:
        print(f"    Extraction photos erreur : {e}")

    return downloaded



# ─────────────────────────────────────────────────────────────
# GENERATION DES FICHIERS OUTPUT
# ─────────────────────────────────────────────────────────────

def merge_reviews(api_reviews: list, scraped: list) -> list:
    """
    Fusionne et deduplique les avis des deux sources.
    Google Maps duplique chaque bloc de review dans le DOM (layout mobile + desktop),
    donc on utilise (auteur_normalisé, debut_texte) comme cle de deduplication.
    """
    normalized = [
        {"author": r.get("author_name",""), "rating": r.get("rating",0),
         "text": r.get("text",""), "date": r.get("relative_time_description",""),
         "profile_photo_url": r.get("profile_photo_url","")}
        for r in api_reviews
    ] + scraped

    seen = set()
    result = []
    for rev in normalized:
        # Normaliser l'auteur : supprimer ponctuation parasite, guillemets, parenthèses
        # ex: "Claire \"claire\"" et "Claire (\"claire\")" → "claire claire"
        raw_author = rev.get("author", "")
        author_key = re.sub(r'[\"\'\(\)\[\]«»]', ' ', raw_author)
        author_key = re.sub(r"\s+", " ", author_key).lower().strip()
        # Ne garder que les 3 premiers mots (prénom + nom) pour ignorer les suffixes Maps
        author_key = " ".join(author_key.split()[:3])

        text_key = re.sub(r"\s+", " ", rev.get("text", "")).lower().strip()[:60]

        # Clé composite auteur + début texte → élimine doublons DOM et homonymes avec textes différents
        key = (author_key, text_key)
        if author_key and key not in seen:
            seen.add(key)
            result.append(rev)
    return result


def build_profile_txt(details: dict, all_reviews: list, all_photos: list) -> str:
    name      = details.get("name", "")
    address   = details.get("formatted_address", "")
    phone     = details.get("formatted_phone_number", "")
    intl      = details.get("international_phone_number", "")
    website   = details.get("website") or "Aucun site web"
    rating    = details.get("rating", 0)
    n_rev     = details.get("user_ratings_total", 0)
    maps_url  = details.get("url", "")
    geo       = details.get("geometry", {}).get("location", {})
    editorial = details.get("editorial_summary", {}).get("overview", "")
    hours     = details.get("opening_hours", {}).get("weekday_text", [])
    types     = [t for t in details.get("types", [])
                 if t not in ("point_of_interest", "establishment")]

    L = []
    L.append("=" * 65)
    L.append(f"  PROFIL COMPLET — {name.upper()}")
    L.append(f"  Genere le {datetime.now().strftime('%d/%m/%Y a %Hh%M')}")
    L.append("=" * 65)
    L.append(f"\nNom            : {name}")
    L.append(f"Adresse        : {address}")
    L.append(f"Tel            : {phone}  ({intl})")
    L.append(f"Site web       : {website}")
    L.append(f"Note           : {stars(rating)}  sur {n_rev} avis")
    L.append(f"Google Maps    : {maps_url}")
    if geo:
        L.append(f"Coordonnees    : {geo.get('lat')}, {geo.get('lng')}")
    if types:
        L.append(f"Categories     : {', '.join(types)}")
    if editorial:
        L.append(f"Description    : {editorial}")

    if hours:
        L.append("\nHoraires :")
        for h in hours:
            L.append(f"  {h}")

    L.append(f"\n── AVIS CLIENTS ({len(all_reviews)} recuperes / {n_rev} au total) ─────────────")
    for i, rev in enumerate(all_reviews, 1):
        L.append(f"\n  [{i:02d}] {rev['author']}  {stars(rev['rating'])}  {rev.get('date','')}")
        if rev.get("text"):
            txt = rev["text"]
            display = txt if len(txt) <= 500 else txt[:500] + "..."
            L.append(f'       "{display}"')

    L.append(f"\n── PHOTOS ({len(all_photos)} telechargees) ────────────────────────────────")
    for p in all_photos:
        size = Path(p).stat().st_size // 1024 if Path(p).exists() else 0
        L.append(f"  {p}  ({size} KB)")

    L.append("\n" + "=" * 65)
    return "\n".join(L)


def build_claude_brief(details: dict, all_reviews: list, all_photos: list, out_dir: Path) -> str:
    name      = details.get("name", "")
    address   = details.get("formatted_address", "")
    phone     = details.get("formatted_phone_number", "")
    website   = details.get("website") or ""
    rating    = details.get("rating", 0)
    n_rev     = details.get("user_ratings_total", 0)
    hours     = details.get("opening_hours", {}).get("weekday_text", [])
    editorial = details.get("editorial_summary", {}).get("overview", "")
    types     = [t for t in details.get("types", [])
                 if t not in ("point_of_interest", "establishment")]

    addr_parts  = address.split(",")
    ville       = addr_parts[-2].strip() if len(addr_parts) >= 2 else ""
    cp_match    = re.search(r"\b\d{5}\b", address)
    code_postal = cp_match.group() if cp_match else ""

    top_reviews = [r for r in all_reviews if r.get("rating",0) >= 4 and r.get("text","").strip()][:8]
    photo_rel   = [str(Path(p).relative_to(out_dir)) for p in all_photos if Path(p).exists()]

    L = []
    L.append("=" * 65)
    L.append("  BRIEF CLAUDE CODE — GENERATION PROMPT STITCH")
    L.append(f"  Business : {name}  |  {ville}  |  {datetime.now().strftime('%d/%m/%Y')}")
    L.append("=" * 65)

    L.append(f"""
INSTRUCTION POUR CLAUDE CODE :
Tu es un expert en design web haute conversion pour artisans locaux francais.

Analyse attentivement :
  1. Toutes les informations ci-dessous sur l'entreprise
  2. Chaque photo dans le dossier photos/ (equipe, vehicules, chantiers, materiel, logo)
  3. Les avis clients reels pour identifier les points forts a mettre en avant

Puis genere un prompt Google Stitch complet, precis et optimise pour creer
le meilleur site web possible pour cette entreprise.

Le prompt Stitch doit etre redige en anglais, etre tres detaille sur :
  - L'analyse des photos disponibles et leur utilisation par section
  - Le design (couleurs inspirees des photos, style, ambiance)
  - La structure exacte de chaque page
  - Les vrais avis clients a integrer verbatim
  - Tous les elements de conversion specifiques au metier
  - Les trust signals adaptes a cette entreprise precise
{"=" * 65}""")

    L.append(f"\n── DONNEES ENTREPRISE ────────────────────────────────────────")
    L.append(f"Nom                : {name}")
    L.append(f"Adresse            : {address}")
    L.append(f"Ville / CP         : {ville}  {code_postal}")
    L.append(f"Telephone          : {phone}")
    L.append(f"Site actuel        : {website if website else '>>> AUCUN SITE WEB <<<'}")
    L.append(f"Note Google        : {rating}/5  ({n_rev} avis)")
    L.append(f"Type de business   : {', '.join(types)}")
    if editorial:
        L.append(f"Description Google : {editorial}")

    if hours:
        L.append("\nHoraires :")
        for h in hours:
            L.append(f"  {h}")

    L.append(f"\n── AVIS A INTEGRER SUR LE SITE ({len(top_reviews)} selectionnes) ─────────────")
    if top_reviews:
        for i, rev in enumerate(top_reviews, 1):
            L.append(f"\n  [{i}] {rev['author']} — {rev.get('rating',5)}/5 — {rev.get('date','')}")
            if rev.get("text"):
                L.append(f'      "{rev["text"][:450]}"')
    else:
        L.append("  Aucun avis avec texte — utiliser des temoignages generiques.")

    L.append(f"\n── PHOTOS DISPONIBLES ({len(all_photos)} fichiers) ────────────────────────")
    L.append(f"Dossier : {out_dir}/photos/\n")
    for p in photo_rel:
        size = ""
        fp = out_dir / p
        if fp.exists():
            kb = fp.stat().st_size // 1024
            size = f"  ({kb} KB)"
        L.append(f"  {p}{size}")

    L.append(f"""
Consigne photos :
  Analyse le CONTENU de chaque photo avant de generer le prompt.
  Identifie : equipe visible ? vehicules avec logo ? chantiers/realisations ?
  materiel professionnel ? facade du local ? logo de l'entreprise ?
  Specifie dans le prompt Stitch quelle photo va dans quelle section du site.
{"=" * 65}""")

    L.append(f"\n── STRUCTURE DU SITE A GENERER (5 pages) ────────────────────")
    L.append(f"""
PAGE 1 — HOMEPAGE :
  Hero section :
    - Headline puissant : "[Metier] a {ville} — [promesse principale]"
    - Sous-titre : benefice cle identifie dans les avis
    - CTA principal : bouton "Devis gratuit" + telephone {phone} visible
    - Utiliser la meilleure photo de realisation ou d'equipe en hero

  Barre de confiance (sous le hero) :
    - Disponibilite (24/7 si applicable selon horaires)
    - Delai de reponse
    - Nombre d'annees d'experience (si mentionnee dans les avis)

  Section services :
    - 3 a 6 cards avec icone + nom service + description courte

  Section avis Google :
    - Afficher NOTE : {rating}/5 avec {n_rev} avis
    - Afficher les {len(top_reviews)} vrais avis ci-dessus verbatim

  Section zone d'intervention :
    - {ville} et villes environnantes ({code_postal[:2]} + departements adjacents)

  Formulaire de contact :
    - Champs : Prenom, Telephone, Email, Type de prestation, Description
    - Bouton : "Envoyer ma demande de devis"

  Footer :
    - Adresse : {address}
    - Tel : {phone}
    - Horaires
    - Liens : Mentions legales, Politique de confidentialite

PAGE 2 — SERVICES :
  Liste detaillee de tous les services avec descriptions et benefices

PAGE 3 — ZONE D'INTERVENTION :
  Carte ou liste des villes couvertes autour de {ville}

PAGE 4 — A PROPOS :
  Histoire entreprise, equipe, certifications, garanties, assurance decennale

PAGE 5 — CONTACT :
  Formulaire complet + adresse + embed carte + telephone + horaires
{"=" * 65}""")

    L.append(f"\n── ELEMENTS DE CONVERSION OBLIGATOIRES ─────────────────────")
    L.append(f"""
Trust signals a inclure :
  - "{n_rev} avis Google verifies ({rating}/5)"
  - "Devis gratuit et sans engagement"
  - "Artisan local" + mention ville {ville}
  - Assurance et garanties (decennale si applicable)
  - Telephone {phone} dans le header, le hero, ET le footer

Design guidelines :
  - Mobile-first absolu (tester chaque section en vue mobile)
  - Couleurs : a determiner en analysant les photos (si vehicule/logo = reprendre ces couleurs)
  - Style : professionnel, de confiance, sobre — pas corporate
  - Langue : francais integralement
  - Appels a l'action : contraste fort, visibles immediatement

SEO :
  - Meta title : "[Service principal] a {ville} — {name} | Devis gratuit"
  - Meta description : mentionner ville, service, disponibilite, telephone
  - JSON-LD LocalBusiness avec toutes les vraies donnees
  - H1 doit contenir "{ville}"
  - Footer avec mentions legales et politique de confidentialite
{"=" * 65}""")

    if not website:
        L.append(f"\nCONTEXTE IMPORTANT :")
        L.append(f"Ce business a {n_rev} avis Google et AUCUN site web.")
        L.append(f"C'est leur toute premiere presence web professionnelle.")
        L.append(f"L'objectif est de capter immediatement tous les clients qui les cherchent sur Google.")
    else:
        L.append(f"\nCONTEXTE : Refonte du site existant {website}")

    L.append("\n" + "=" * 65)
    return "\n".join(L)


# ─────────────────────────────────────────────────────────────
# NOUVEAUX GENERATEURS — WORKFLOW CLAUDE CODE DIRECT
# ─────────────────────────────────────────────────────────────

def parse_hours_to_js_object(weekday_text: list) -> str:
    """
    Convertit la liste Google Places weekday_text en objet JS pour le widget
    temps-réel des horaires.

    Format Google : ["Lundi: 08:00 – 18:00", "Mardi: Fermé",
                     "Mercredi: 08:00 – 12:00, 14:00 – 18:00", ...]
    Format JS cible (SUPPORT SPLIT-SHIFT) :
      const BUSINESS_HOURS = {
        1: [{ open: "08:00", close: "18:00" }],                                   // Lundi
        2: null,                                                                   // Mardi fermé
        3: [{ open: "08:00", close: "12:00" }, { open: "14:00", close: "18:00" }],// Mercredi pause midi
        ...
        0: null,                                                                   // Dimanche
      };
    JS convention : 0=Dimanche, 1=Lundi, ..., 6=Samedi
    Chaque jour ouvert = ARRAY d'intervalles (même un seul intervalle).
    """
    DAY_MAP = {
        "lundi": 1, "mardi": 2, "mercredi": 3, "jeudi": 4,
        "vendredi": 5, "samedi": 6, "dimanche": 0,
        "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4,
        "friday": 5, "saturday": 6, "sunday": 0,
    }

    parsed = {}
    for line in weekday_text:
        line_lower = line.lower()
        day_num = None
        for day_name, num in DAY_MAP.items():
            if line_lower.startswith(day_name):
                day_num = num
                break
        if day_num is None:
            continue

        # Chercher TOUS les intervalles HH:MM – HH:MM dans la ligne (split-shift)
        intervals_raw = re.findall(
            r"(\d{1,2})[h:H](\d{0,2})\s*[–\-—]\s*(\d{1,2})[h:H](\d{0,2})",
            line
        )
        if intervals_raw:
            intervals = []
            for oh, om, ch, cm in intervals_raw:
                open_t  = f"{int(oh):02d}:{om.zfill(2) if om else '00'}"
                close_t = f"{int(ch):02d}:{cm.zfill(2) if cm else '00'}"
                intervals.append({"open": open_t, "close": close_t})
            parsed[day_num] = intervals
        else:
            parsed[day_num] = None

    # Construire la chaîne JS — TOUJOURS un array, même pour un seul intervalle
    lines = ["const BUSINESS_HOURS = {"]
    day_labels = {
        0: "Dimanche", 1: "Lundi", 2: "Mardi", 3: "Mercredi",
        4: "Jeudi", 5: "Vendredi", 6: "Samedi",
    }
    for num in range(7):
        label = day_labels[num]
        if num in parsed and parsed[num]:
            intervals = parsed[num]
            js_intervals = ", ".join(
                f'{{ open: "{iv["open"]}", close: "{iv["close"]}" }}'
                for iv in intervals
            )
            note = " (pause méridienne)" if len(intervals) > 1 else ""
            lines.append(f"  {num}: [{js_intervals}],  // {label}{note}")
        else:
            lines.append(f"  {num}: null,  // {label} — Fermé")
    lines.append("};")
    return "\n".join(lines)


def generate_reviews_json(all_reviews: list, out_dir: Path) -> list:
    """
    Génère reviews.json dans le format attendu par le frontend.
    Sélectionne les meilleurs avis (4-5 étoiles, avec texte).
    Retourne la liste des avis exportés.
    """
    MONTH_MAP = {
        "janvier": "Janvier", "février": "Février", "mars": "Mars",
        "avril": "Avril", "mai": "Mai", "juin": "Juin",
        "juillet": "Juillet", "août": "Août", "septembre": "Septembre",
        "octobre": "Octobre", "novembre": "Novembre", "décembre": "Décembre",
        "january": "Janvier", "february": "Février", "march": "Mars",
        "april": "Avril", "may": "Mai", "june": "Juin",
        "july": "Juillet", "august": "Août", "september": "Septembre",
        "october": "Octobre", "november": "Novembre", "december": "Décembre",
    }

    # Sélectionner les avis de qualité
    quality = [r for r in all_reviews if r.get("rating", 0) >= 4 and len(r.get("text", "").strip()) > 30]
    if not quality:
        quality = [r for r in all_reviews if r.get("text", "").strip()]
    best = quality[:MAX_REVIEWS_DISPLAY]

    exported = []
    for rev in best:
        author = rev.get("author", "Client")
        date_raw = rev.get("date", "")

        # Construire le champ "role" = "Ville · Mois Année" ou juste la date relative
        role_parts = []
        # Essayer de normaliser la date
        date_norm = date_raw
        for fr, display in MONTH_MAP.items():
            date_norm = re.sub(fr, display, date_norm, flags=re.IGNORECASE)
        if date_norm:
            role_parts.append(date_norm)
        role = " · ".join(role_parts) if role_parts else "Avis vérifié"

        text = rev.get("text", "")
        # Tronquer à 300 chars max pour l'affichage carte
        if len(text) > 300:
            text = text[:297] + "…"

        # Avatar : URL Google si disponible, sinon placeholder SVG basé sur initiales
        image_url = rev.get("profile_photo_url", "")
        if not image_url:
            initial = author[0].upper() if author else "C"
            image_url = f"https://ui-avatars.com/api/?name={quote_plus(author)}&background=random&color=fff&size=64"

        exported.append({
            "name":   author,
            "role":   role,
            "rating": rev.get("rating", 5),
            "text":   text,
            "image":  image_url,
        })

    out_path = out_dir / "reviews.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(exported, f, ensure_ascii=False, indent=2)
    print(f"    reviews.json   — {len(exported)} avis exportés")
    return exported


def generate_reviews_meta_json(details: dict, out_dir: Path) -> None:
    """
    Génère reviews_meta.json — source de vérité pour le compteur d'avis dynamique.
    Le site JS fetche ce fichier au chargement et met à jour tous les .js-review-count.
    """
    place_id   = details.get("place_id", "")
    rating     = details.get("rating", 0)
    n_rev      = details.get("user_ratings_total", 0)
    review_url = (
        f"https://search.google.com/local/writereview?placeid={place_id}"
        if place_id else ""
    )
    meta = {
        "rating":     rating,
        "count":      n_rev,
        "place_id":   place_id,
        "review_url": review_url,
    }
    with open(out_dir / "reviews_meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


def generate_reviews_inline_js(exported_reviews: list, meta: dict, out_dir: Path) -> None:
    """
    Génère reviews_inline.js — embed des reviews directement dans le JS.
    Évite tout fetch() async pour du contenu critique above-the-fold.
    Fonctionne en file:// (pas de CORS), pas de race condition au screenshot.
    Le script doit être inclus AVANT les composants Alpine qui en dépendent.
    """
    reviews_js = json.dumps(exported_reviews, ensure_ascii=False, indent=2)
    meta_js    = json.dumps(meta, ensure_ascii=False, indent=2)
    content = (
        "// reviews_inline.js — embed des avis pour usage immediat (pas de fetch async)\n"
        "// Inclure dans chaque page AVANT les scripts de composants : <script src='reviews_inline.js'></script>\n"
        "\n"
        f"window.SITE_REVIEWS = {reviews_js};\n"
        "\n"
        f"window.SITE_REVIEWS_META = {meta_js};\n"
        "\n"
        "// Mise a jour des compteurs DOM (.js-review-count, .js-review-rating, a.js-review-url)\n"
        "document.addEventListener('DOMContentLoaded', () => {\n"
        "  const m = window.SITE_REVIEWS_META || {};\n"
        "  document.querySelectorAll('.js-review-count').forEach(el => el.textContent = m.count);\n"
        "  document.querySelectorAll('.js-review-rating').forEach(el => el.textContent = m.rating);\n"
        "  document.querySelectorAll('a.js-review-url').forEach(el => { if (m.review_url) el.href = m.review_url; });\n"
        "});\n"
    )
    with open(out_dir / "reviews_inline.js", "w", encoding="utf-8") as f:
        f.write(content)
    print(f"    reviews_inline.js -- {len(exported_reviews)} avis embeds (pas de fetch async)")


def build_product_md(details: dict, all_photos: list, out_dir: Path) -> str:
    """
    Génère PRODUCT.md au format exact attendu par le skill 'impeccable'.
    Sections requises (ordre fixe) :
        Register · Users · Product Purpose · Brand Personality
        Anti-references · Design Principles · Accessibility & Inclusion
    """
    name      = details.get("name", "")
    address   = details.get("formatted_address", "")
    phone     = details.get("formatted_phone_number", "")
    rating    = details.get("rating", 0)
    n_rev     = details.get("user_ratings_total", 0)
    editorial = details.get("editorial_summary", {}).get("overview", "")
    types     = [t for t in details.get("types", [])
                 if t not in ("point_of_interest", "establishment")]
    website   = details.get("website") or ""

    addr_parts  = address.split(",")
    ville       = addr_parts[-2].strip() if len(addr_parts) >= 2 else address
    cp_match    = re.search(r"\b\d{5}\b", address)
    code_postal = cp_match.group() if cp_match else ""

    METIER_MAP = {
        "plumber":              "Plomberie & Chauffage",
        "electrician":          "Électricité",
        "roofing_contractor":   "Couverture & Toiture",
        "painter":              "Peinture & Décoration",
        "general_contractor":   "Entreprise Générale du Bâtiment",
        "landscaper":           "Paysagisme & Jardins",
        "locksmith":            "Serrurerie",
        "hvac_contractor":      "Chauffage / Climatisation",
        "carpenter":            "Menuiserie & Charpente",
        "tiler":                "Carrelage & Sols",
        "moving_company":       "Déménagement",
        "cleaning_service":     "Nettoyage & Entretien",
    }
    metier = next(
        (METIER_MAP[t] for t in types if t in METIER_MAP),
        types[0].replace("_", " ").title() if types else "Artisan"
    )

    # Description : éditoriale Google ou inférée
    desc = editorial if editorial else (
        f"Artisan local en {metier} basé à {ville}, "
        f"noté {rating}/5 sur {n_rev} avis Google vérifiés."
    )

    parts = [
        f"# Product",
        "",
        "## Register",
        "brand",
        "",
        "## Users",
        (
            f"Particuliers à {ville} ({code_postal}) et communes voisines cherchant un "
            f"{metier.lower()} de confiance. Deux profils coexistent : urgence immédiate "
            f"(fuite, panne, dépannage) et projet planifié (rénovation, installation). "
            f"Mobile-first — 70 % des visites viennent de smartphone. "
            f"Le visiteur décide d'appeler en moins de 30 secondes : il ne lit pas, il scanne."
        ),
        "",
        "## Product Purpose",
        (
            f"Site vitrine de conversion pour {name}, {metier.lower()} basé à {ville}. "
            f"{desc} "
            f"L'objectif unique est de transformer les visiteurs Google en appels téléphoniques "
            f"et demandes de devis. Ce n'est pas un portfolio — c'est un outil de prospection. "
            f"Le succès se mesure au nombre d'appels générés, pas au temps passé sur le site."
        ),
        "",
        "## Brand Personality",
        (
            f"Professionnel local, direct, rassurant. Ni corporate ni décontracté. "
            f"La marque repose sur trois piliers : proximité géographique réelle ({ville}), "
            f"preuve sociale tangible ({n_rev} avis Google vérifiés à {rating}/5), "
            f"et réactivité (devis rapide, dépannage urgent). "
            f"Ton : un artisan compétent qui parle vrai, pas un commercial."
        ),
        "",
        "## Anti-references",
        "- Site WordPress template générique avec stock photos",
        "- Palette bleue/blanche générique 'plombier du coin'",
        "- Hero avec photo de gouttelettes d'eau ou ampoule",
        "- Texte plein de « Qualité », « Excellence », « Expertise » sans preuve",
        "- 3 colonnes égales de services identiques (card grid uniforme)",
        "- Formulaire de contact invisible en bas de page",
        "- Footer chargé de liens inutiles",
        "- Animations de chargement sans raison",
        "- Dégradé texte décoratif (background-clip: text)",
        "- Bordure colorée latérale sur les cards (side-stripe)",
        "",
        "## Design Principles",
        (
            "1. **Appel avant tout** — le numéro de téléphone est visible en permanence, "
            "dès le header sticky. Chaque section possède un CTA explicite."
        ),
        (
            "2. **Authenticité locale** — les photos réelles de l'artisan, de son véhicule "
            "et de ses chantiers priment sur tout stock photo. Le design découle des couleurs "
            "extraites de ces photos, pas d'un template secteur."
        ),
        (
            f"3. **Preuve sociale au premier plan** — les {n_rev} avis Google ({rating}/5) "
            f"apparaissent dans le hero et jalonnent le parcours de conversion, pas uniquement en bas de page."
        ),
        (
            "4. **Mobile-first sans compromis** — chaque décision de layout, de typographie "
            "et de CTA est validée d'abord sur 390px. Les breakpoints supérieurs enrichissent, "
            "ils ne rattrapent pas."
        ),
        (
            "5. **Personnalité forte, non générique** — le site doit être immédiatement "
            "reconnaissable comme différent d'un template artisan standard. "
            "La palette couleur sort des clichés bleu/blanc du secteur."
        ),
        "",
        "## Accessibility & Inclusion",
        (
            "WCAG 2.1 AA minimum. Contraste texte/fond ≥ 4.5:1 (corps), ≥ 3:1 (grands titres). "
            "Tous les CTAs téléphoniques utilisent href=\"tel:...\" natif (accessibilité + iOS tap-to-call). "
            "Animations soumises à prefers-reduced-motion. "
            "Images avec attribut alt descriptif. Formulaire avec labels associés aux champs."
        ),
        "",
        "---",
        "",
        "## Référence Métier",
        f"- **Entreprise** : {name}",
        f"- **Adresse** : {address}",
        f"- **Téléphone** : {phone}",
        f"- **Note Google** : {rating}/5 sur {n_rev} avis",
        f"- **Site existant** : {website if website else 'Aucun — première présence web'}",
    ]

    if types:
        parts.append(f"- **Types Google** : {', '.join(types)}")

    parts += [
        "",
        "## Photos & Vidéos",
        "Voir CLAUDE.md §4 — protocole complet d'analyse médias.",
    ]

    return "\n".join(parts)


def build_claude_md(
    details: dict,
    all_reviews: list,
    all_photos: list,
    out_dir: Path,
) -> str:
    """
    Génère le CLAUDE.md — document conducteur complet pour Claude Code.
    Claude Code lit ce fichier au démarrage et suit toutes ses instructions
    pour produire le site final parfait en une seule passe.
    """
    name      = details.get("name", "")
    address   = details.get("formatted_address", "")
    phone     = details.get("formatted_phone_number", "")
    intl      = details.get("international_phone_number", "")
    website   = details.get("website") or ""
    rating    = details.get("rating", 0)
    n_rev     = details.get("user_ratings_total", 0)
    maps_url  = details.get("url", "")
    geo       = details.get("geometry", {}).get("location", {})
    lat       = geo.get("lat", "")
    lng       = geo.get("lng", "")
    editorial = details.get("editorial_summary", {}).get("overview", "")
    hours_raw = details.get("opening_hours", {}).get("weekday_text", [])
    types     = [t for t in details.get("types", [])
                 if t not in ("point_of_interest", "establishment")]

    addr_parts  = address.split(",")
    ville       = addr_parts[-2].strip() if len(addr_parts) >= 2 else address
    dept_raw    = addr_parts[-1].strip() if len(addr_parts) >= 1 else ""
    cp_match    = re.search(r"\b\d{5}\b", address)
    code_postal = cp_match.group() if cp_match else ""
    dept_num    = code_postal[:2] if code_postal else ""
    street_addr = next(
        (p.strip() for p in addr_parts
         if "@" not in p and not re.search(r"\b\d{5}\b", p) and p.strip() not in (ville, dept_raw, "")),
        addr_parts[0].strip() if addr_parts else address
    )

    METIER_MAP = {
        "plumber": ("Plomberie & Chauffage", "plombier", "plomberie chauffage sanitaire"),
        "electrician": ("Électricité", "électricien", "électricité installation dépannage"),
        "roofing_contractor": ("Couverture & Toiture", "couvreur", "couverture toiture zinguerie"),
        "painter": ("Peinture & Décoration", "peintre", "peinture intérieure extérieure"),
        "general_contractor": ("Bâtiment", "artisan", "rénovation construction travaux"),
        "landscaper": ("Paysagisme", "paysagiste", "jardins espaces verts entretien"),
        "locksmith": ("Serrurerie", "serrurier", "serrurerie dépannage urgence"),
        "hvac_contractor": ("Chauffage / Clim", "chauffagiste", "chauffage climatisation VMC"),
        "carpenter": ("Menuiserie", "menuisier", "menuiserie charpente bois"),
        "tiler": ("Carrelage", "carreleur", "carrelage sol mur faïence"),
        "moving_company": ("Déménagement", "déménageur", "déménagement transport stockage"),
        "cleaning_service": ("Nettoyage", "agent d'entretien", "nettoyage entretien ménage"),
    }
    metier_tuple = next((METIER_MAP[t] for t in types if t in METIER_MAP),
                        ("Artisan BTP", "artisan", "travaux rénovation"))
    metier_label, metier_singulier, metier_keywords = metier_tuple

    # Heures JS
    hours_js = parse_hours_to_js_object(hours_raw)

    # Top avis pour insertion verbatim
    top_reviews = [r for r in all_reviews if r.get("rating", 0) >= 4 and len(r.get("text", "").strip()) > 40][:5]

    # Photos relatives
    photo_rel = [f"photos/{Path(p).name}" for p in all_photos if Path(p).exists()]

    # Département/zone
    zone_str = f"{ville} et les communes du {dept_num}" if dept_num else ville

    # Formsubmit email (utiliser l'email courant ou placeholder)
    formsubmit_email = "__CLIENT_EMAIL__"

    # ── Blocs placeholders (legal + social) — à remplir manuellement ────────────
    legal_block  = _build_legal_block(name, address)
    social_block = _build_social_block(name)

    # ── Construction du document ────────────────────────────────────────────────

    doc = f"""# CLAUDE.md — {name}
# Conducteur de build : site vitrine haute conversion pour artisan local
# Généré automatiquement le {datetime.now().strftime("%d/%m/%Y à %Hh%M")}
# ⚠️  Ne pas modifier manuellement — regénérer via company_scraper_bis.py

---

## 0. MINDSET & BARRE DE QUALITÉ

> 🚨 **AVERTISSEMENT LÉGAL CRITIQUE — À LIRE EN PREMIER 🚨**
>
> Tu écris pour **{name}**, qui sera **légalement responsable** de chaque mot publié sur ce site.
> Inventer une date de création ("depuis 1995"), un nom d'employé ("Bruno chef d'équipe"), un prix
> ("à partir de 25 000 €"), une garantie ("décennale 10 ans"), une certification ("RGE Qualibat")
> que le client ne possède pas, c'est de la **publicité mensongère** :
> - Code de la consommation art. L.121-2 : jusqu'à **2 ans de prison + 300 000 €** d'amende
> - DGCCRF peut sanctionner le client sur simple signalement
> - Risque de diffamation si un employé nommé n'existe pas
>
> **RÈGLE ABSOLUE** : tout fait spécifique (date, nom, chiffre, garantie, prix, durée, %)
> doit être **sourcé** dans les données scrapées (profil.json, reviews_all.json, reviews_meta.json)
> OU citer littéralement un avis. Sinon : **INTERDIT** d'écrire ce fait.
>
> Voir §5bis pour le protocole anti-hallucination détaillé. Le script `audit_content.py`
> vérifie automatiquement chaque fait du HTML contre les sources avant deploy.

---

### Au-delà du légal — la mission design

> **Tu n'es pas en train de "suivre des specs". Tu es un designer senior.**
>
> Le site que tu construis pour **{name}** va être ouvert par des particuliers qui jugent
> en 3 secondes si l'artisan a l'air sérieux ou bricolé. Chaque pixel mal placé, chaque gap
> qui sent le template gratuit, chaque vitesse d'animation maladroite leur donne une raison
> de fermer l'onglet.
>
> **Ta vraie mission, ce n'est pas d'écrire du HTML qui marche — c'est de REGARDER ton écran
> à chaque section et te demander : "est-ce que ça a l'air cher ou cheap ?".**
> Si tu hésites, c'est que c'est cheap. Recommence.

### Référence de qualité (étalon mental)

Ce site doit pouvoir tenir à côté d'une landing page de **Linear, Vercel, Stripe, ou Arc Browser**.
Pas dans le style — dans le **niveau de soin**. Pas dans le contenu — dans la **rigueur de chaque détail**.

Quand tu finis une section, pose-toi la question :
*"Si Linear avait fait ce site pour un artisan, est-ce qu'il ressemblerait à ça ?"*
Si la réponse est non → ce n'est pas fini.

### Ce que "cher" veut dire concrètement

- Aucun élément ne flotte tout seul sans raison (badge sur le côté, texte coincé à gauche d'un vide)
- Aucune animation maladroite (marquee trop rapide, transitions cassées, easing par défaut)
- Aucun détail "presque bon" — un gap qui devrait être 64px et qui est à 40px = à corriger
- Cohérence des espacements, des tailles, des rayons de coin partout
- Mobile et desktop également soignés — pas un mobile en chantier
- Les photos respirent, ne sont jamais étirées ni coupées maladroitement
- Le texte mobile prend la largeur disponible, jamais 3 lignes à gauche d'un vide

### Critères de succès visiteur

Un visiteur smartphone qui arrive depuis Google doit pouvoir :
1. Comprendre le métier et la zone en **< 3 secondes**
2. Appeler ou demander un devis en **< 2 clics**
3. Être convaincu par la preuve sociale en **< 30 secondes** de scroll

**Valeur cible** : Site professionnel vendu **800–1200€**. Le prix se justifie par le niveau de soin,
pas par le nombre de pages. Mieux vaut 4 pages parfaites que 8 pages moyennes.

---

## 1. SÉQUENCE DE SKILLS — ORDRE STRICT, JAMAIS SIMULTANÉ

Invoquer les skills dans cet ordre exact. Attendre la fin complète de chacun avant de passer au suivant.

| Étape | Skill | Commande | Rôle |
|-------|-------|----------|------|
| 1 | `full-output-enforcement` | (auto) | Interdit toute troncature — activer en premier |
| 2 | `stitch-design-taste` | (auto) | Générer DESIGN.md après analyse photos |
| 3 | `impeccable` | `teach` → `craft` → `polish` → `audit` | Build principal du site |
| 4 | `emil-design-eng` | (auto dans polish) | Animations, spring physics, micro-interactions |

**Règle absolue** : Ne jamais activer deux skills simultanément. Chaque skill modifie le contexte de génération — les superposer produit des conflits et du slop.

---

## 2. DONNÉES ENTREPRISE

```
Nom                : {name}
Métier             : {metier_label}
Adresse            : {address}
Ville / CP         : {ville} {code_postal}
Téléphone FR       : {phone}
Téléphone Intl     : {intl}
Note Google        : {rating}/5 sur {n_rev} avis
Google Maps URL    : {maps_url}
Coordonnées GPS    : {lat}, {lng}
Site existant      : {website if website else "AUCUN — première présence web"}
```
"""

    if editorial:
        doc += f"""
Description Google : {editorial}
"""

    doc += f"""
---

## 3. HORAIRES D'OUVERTURE

### Format texte (pour affichage HTML)
"""
    for h in hours_raw:
        doc += f"- {h}\n"

    doc += f"""
### Objet JS (pour le widget statut temps-réel)
Coller tel quel dans le script du widget horaires :

```javascript
{hours_js}

// BUSINESS_HOURS[day] est SOIT null (ferme), SOIT un ARRAY d'intervalles
//   [{{ open: "08:00", close: "12:00" }}, {{ open: "14:00", close: "18:00" }}]
// (support pause meridienne / split-shift artisan)

function getNextOpen() {{
  const now = new Date();
  let d = (now.getDay() + 1) % 7;
  const dayNames = ['dim.', 'lun.', 'mar.', 'mer.', 'jeu.', 'ven.', 'sam.'];
  for (let i = 0; i < 6; i++) {{
    const intervals = BUSINESS_HOURS[d];
    if (intervals && intervals.length > 0) {{
      return (i === 0 ? 'demain' : dayNames[d]) + ' à ' + intervals[0].open;
    }}
    d = (d + 1) % 7;
  }}
  return 'prochainement';
}}

// Fonction de vérification du statut (support split-shift)
function getBusinessStatus() {{
  const now = new Date();
  const day = now.getDay();  // 0=Dim, 1=Lun...
  const timeStr = now.getHours().toString().padStart(2,'0') + ':' + now.getMinutes().toString().padStart(2,'0');
  const intervals = BUSINESS_HOURS[day];

  if (!intervals || intervals.length === 0) {{
    return {{ open: false, label: 'Fermé aujourd\\'hui' }};
  }}

  // 1. Cherche un intervalle qui contient l'heure actuelle (ouvert MAINTENANT)
  for (const iv of intervals) {{
    if (timeStr >= iv.open && timeStr < iv.close) {{
      return {{ open: true, label: `Ouvert · Ferme à ${{iv.close}}` }};
    }}
  }}

  // 2. Cherche le prochain intervalle du jour qui ouvre apres maintenant
  //    (cas pause meridienne : on est entre 12h et 14h, prochain ouvre a 14h)
  for (const iv of intervals) {{
    if (timeStr < iv.open) {{
      return {{ open: false, label: `Ouvre à ${{iv.open}}` }};
    }}
  }}

  // 3. Sinon (apres le dernier intervalle), ouvre le prochain jour
  return {{ open: false, label: `Fermé · Ouvre ${{getNextOpen()}}` }};
}}
```

---

## 4. PROTOCOLE MÉDIAS — PHOTOS & VIDÉOS (GATE BLOQUANT)

> ⚠️ **GATE OBLIGATOIRE — INTERDICTION DE PASSER AVANT D'AVOIR FAIT LE TRAVAIL.**
>
> Tu DOIS analyser visuellement **CHAQUE FICHIER** dans `photos/` et `videos/`.
> Pas un échantillon. Pas "les principales". **TOUS**.
>
> Le manifest.json doit contenir **une entrée par fichier**. Le compteur ci-dessous
> doit afficher `Photos analysées : N/N` (avec N = total réel du dossier) avant de continuer.
> Si tu sautes une photo, le site sera mal monté — c'est garanti.

### Template d'analyse — à remplir LITTÉRALEMENT pour chaque photo

```
─────────────────────────────────────────────────────
PHOTO : photos/photo_XX.jpg
─────────────────────────────────────────────────────
Contenu     : [description précise de ce qui est visible en 1 phrase]
Couleurs    : [3 hex codes dominants]
Qualité     : [high | medium | low] + raison
Affectation : [hero | services/X | gallery | about | background | logo]
Justification: [pourquoi cette affectation et pas une autre, 1 phrase]
─────────────────────────────────────────────────────
```

Une fois TOUTES les photos analysées, écrire le compteur final :
`Photos analysées : N/N ✓` (où N = nombre réel de fichiers dans photos/)
**Sans ce compteur visible et complet, ne pas générer manifest.json.**

---

### Étape 0 — Détection du logo (prioritaire)

**Première action — avant tout autre chose** :
```bash
ls photos/logo.png 2>/dev/null && echo "LOGO PRESENT" || echo "LOGO ABSENT"
```

- **Si `photos/logo.png` existe** → noter immédiatement. Ce fichier sera l'unique source du logo dans tout le site : header, footer, favicon éventuel. Affectation dans manifest.json : `"logo"`. Ne jamais utiliser `logo.png` comme fond ou en galerie.
- **Si absent** → utiliser le nom de l'entreprise en texte (font display, couleur de marque) dans header et footer.

---

### Étape 1 — Inventaire complet

Lancer ces deux commandes :
```bash
ls photos/    # toutes les photos disponibles
ls videos/    # toutes les vidéos disponibles (peut être vide — pas grave)
```

> ⚠️ **GATE VIDÉOS — VÉRIFICATION BLOQUANTE**
> Documenter le résultat de `ls videos/` avant de continuer — même si tu penses que le dossier est vide.
> Si des fichiers `.mp4` sont présents → l'Étape 3 (analyse vidéos) est **obligatoire et non-optionnelle**.
> Ne jamais sauter cette commande. Ne jamais supposer que le dossier est vide sans l'avoir vérifié.

Traiter tout ce qui apparaît dans ces deux dossiers. Jamais ignorer `videos/` sous prétexte
que le dossier était vide au moment de la génération de ce fichier — des vidéos ont pu
être ajoutées manuellement depuis.

---

### Étape 2 — Analyse des photos

Pour chaque fichier dans `photos/`, noter :
- **Couleurs dominantes** : hex codes des couleurs de marque (véhicule, logo, combinaison, panneau)
- **Contenu** : équipe ? véhicule avec logo ? chantier ? matériel ? local commercial ?
- **Qualité** : nette et utilisable en production ? ou trop sombre / floue ?
- **Affectation** : hero | à-propos | réalisations | fond overlay

### Règles d'affectation photos
- **Photo hero** : meilleure réalisation OU équipe en action — jamais photo d'outil isolé
- **Section À propos** : équipe, local, véhicule de marque
- **Section Réalisations** : chantiers avant/après ou travaux terminés
- **Photos de fond** : `background-image` avec overlay sombre 50 % pour lisibilité

---

### Étape 3 — Analyse des vidéos (si `videos/` contient des fichiers)

Pour chaque `video_XX.mp4`, extraire **1 frame toutes les 2 secondes** (couverture complète — aucun angle mort) :

```bash
DURATION=$(ffprobe -v error -show_entries format=duration \
  -of default=noprint_wrappers=1:nokey=1 videos/video_01.mp4 2>/dev/null | cut -d. -f1)

# Intervalle adaptatif : 2s si vidéo ≤ 60s, 4s si > 60s — toujours 15-20 frames
INTERVAL=$(( DURATION / 15 ))
[ "$INTERVAL" -lt 2 ] && INTERVAL=2

T=0
IDX=0
while [ "$T" -le "$DURATION" ]; do
  ffmpeg -i videos/video_01.mp4 -ss $T -vframes 1 \
    /tmp/frame_v01_$(printf '%02d' $IDX)_${{T}}s.jpg -y 2>/dev/null
  T=$(( T + INTERVAL ))
  IDX=$(( IDX + 1 ))
done
```

> Pourquoi cette approche : les pourcentages fixes ratent les frames cruciales entre deux checkpoints
> (ex : camionnette de marque visible à 4s sur une vidéo de 26s, invisible avec 5%/20%/35%).
> 1 frame / 2s garantit qu'aucun élément de branding (véhicule, logo, tenue) n'est ignoré.

Analyser **chaque frame** comme une photo :
- **Couleurs** → ajouter à la palette de marque si nouvelles couleurs détectées
- **Contenu** : équipe en action ? chantier ? véhicule avec logo ? client qui témoigne ?
- **Qualité** : frame nette et exploitable, ou trop floue / sombre ?

> Si `ffmpeg` indisponible : inférer le contenu depuis le nom/poids du fichier et appliquer
> les règles d'affectation par défaut ci-dessous.

### Règles d'affectation vidéos et composants HTML

**Décision d'affectation — dans l'ordre :**

1. **Hero vidéo background** — si la vidéo montre un chantier ou l'équipe en action, ≥ 720p
```html
<section class="relative min-h-[100dvh] flex items-center overflow-hidden">
  <video autoplay muted loop playsinline
    class="absolute inset-0 w-full h-full object-cover"
    poster="photos/photo_01.jpg">
    <source src="videos/video_01.mp4" type="video/mp4">
    <img src="photos/photo_01.jpg" alt="Artisan au travail — {name}">
  </video>
  <div class="absolute inset-0 bg-black/55"></div>
  <div class="relative z-10"><!-- contenu hero --></div>
</section>
```

2. **Galerie Réalisations** — si la vidéo montre des chantiers/travaux terminés
```html
<video controls playsinline preload="none"
  class="w-full rounded-2xl shadow-xl aspect-video object-cover"
  poster="photos/photo_02.jpg">
  <source src="videos/video_01.mp4" type="video/mp4">
</video>
```

3. **Témoignage client vidéo** — si un client parle face caméra
```html
<div class="relative rounded-2xl overflow-hidden bg-gray-900 aspect-video">
  <video controls playsinline preload="none" poster="photos/photo_03.jpg" class="w-full h-full object-cover">
    <source src="videos/video_01.mp4" type="video/mp4">
  </video>
  <div class="absolute bottom-4 left-4 text-white">
    <p class="font-semibold text-sm">Avis vidéo vérifié</p>
  </div>
</div>
```

**Règles techniques (BLOQUANTES) :**
- ✅ `autoplay` → toujours accompagné de `muted` (bloqué par tous les navigateurs sinon)
- ✅ `playsinline` → obligatoire pour iOS Safari
- ✅ `poster` → toujours renseigné (affichage avant chargement) — **obligatoire** pour preload="none"
- ✅ `preload="metadata"` → AUTORISÉ UNIQUEMENT sur la vidéo HERO (above the fold)
- ✅ `preload="none"` → **OBLIGATOIRE** sur toutes les autres vidéos (galeries, témoignages, etc.)
- ❌ Autoplay avec son → UX catastrophique, jamais
- ❌ Vidéo de mauvaise qualité en hero → dégrader en photo background avec `poster`
- ❌ Plusieurs vidéos avec `preload="metadata"` sur la même page → bloque Playwright (timeout networkidle)
  et ralentit le chargement initial sur mobile. **Une seule** vidéo metadata par page max.

---

### Étape 4 — Palette de marque consolidée

Après analyse photos + frames vidéos, définir la palette finale :
- **Couleur principale** : couleur la plus présente sur véhicule / logo / tenue
- **Couleur secondaire** : contraste ou couleur d'accroche (souvent un jaune, orange, rouge)
- **Fallback** : `#1E3A5F` (bleu marine) + `#F59E0B` (ambre) si aucune couleur de marque détectable

---

### Étape 5 — Écriture du manifest photos (OBLIGATOIRE avant toute construction)

Après avoir analysé toutes les photos et frames vidéo, écrire `photos/manifest.json`.

Ce fichier est la **seule source de vérité** pour l'affectation des photos.
Interdiction absolue d'insérer une photo sans avoir consulté ce fichier.

**Format requis** :
```json
[
  {{
    "file": "photo_01.jpg",
    "description": "Description précise en une phrase de ce que montre réellement cette photo",
    "service_category": "chauffage | plomberie | recherche_fuite | climatisation | depannage | equipe | vehicule | chantier_general | logo",
    "quality": "high | medium | low",
    "affectation": "hero | services/chauffage | services/plomberie | services/recherche_fuite | services/climatisation | gallery | about | background | logo",
    "usable_as_hero": true
  }}
]
```

**Règles d'affectation strictes** :
- `logo` → réservé à `photos/logo.png` uniquement. Ne jamais servir comme fond ou galerie.
- `hero` → meilleure photo de chantier impressionnant OU équipe en action. `"usable_as_hero": true` obligatoire.
- `services/[categorie]` → photo qui illustre DIRECTEMENT ce service. Jamais approximatif.
  - `services/recherche_fuite` → uniquement si la photo montre une fuite enterrée, excavation, tuyau percé, sol ouvert.
  - `services/chauffage` → uniquement chaudière, radiateur, ballon, plancher chauffant.
  - `services/plomberie` → tuyauterie, robinetterie, évier, WC, salle de bain.
- `gallery` → réalisations sans catégorie précise, photos de bonne qualité.
- `about` → équipe, véhicule, local commercial, artisan en portrait.
- `background` → fond acceptable avec overlay sombre.

**Règle absolue** : si aucune photo ne correspond à un service → utiliser `gallery` ou `chantier_general`.
**Jamais** insérer une photo incorrecte pour "remplir" une section.

**Pendant la construction (§6 PHASE 3)** — pour chaque section :
1. Lire `photos/manifest.json`
2. Filtrer par `service_category` ou `affectation` exact
3. Choisir la photo avec `quality: "high"` en priorité
4. Si aucun match exact → utiliser une photo `gallery` plutôt qu'une photo de mauvaise catégorie

---

## 4ter. DÉCISIONS DESIGN — CONCEPT + DESIGN.md + LAYOUT (GATE BLOQUANT)

> ⚠️ **Compléter les 3 blocs ci-dessous AVANT toute écriture de HTML.**
> Un designer ne commence pas par coder — il décide d'abord le concept, les tokens, l'architecture.
> Sans ces 3 outputs documentés, la PHASE 3 produit un template générique.

### Bloc 1 — CONCEPT DESIGN (à écrire dans ton message avant de coder)

```
CONCEPT DESIGN
─────────────────────────────────────────────────────────
Phrase-concept    : [Personnalité — source palette — énergie typo — niveau déco]
                    Exemple : "Artisan direct sans fioritures, palette extraite du gris
                    anthracite du van et cuivre des tuyaux, typo lourde et affirmée."
Stratégie couleur : Restrained | Committed | Full palette | Drenched
                    (Committed = défaut artisans : primary sur 30-60 % des surfaces)
Direction typo    : Éditoriale | Fonctionnelle | Expressive
                    (doit découler de la phrase-concept)
Phrase de scène   : Qui ouvre le site, où, dans quel état, sous quelle lumière
                    (force le choix clair/sombre)
Thème             : Clair | Sombre
─────────────────────────────────────────────────────────
```

**Polices** :
- Autorisées : Space Grotesk, Bricolage Grotesque, Geist, Satoshi, Cabinet Grotesk, Unbounded, Lexend, Manrope, Figtree, Nunito Sans
- Bannies : Inter, Outfit, DM Sans, Plus Jakarta Sans, Instrument Sans, Syne, IBM Plex Sans, Lora, Playfair Display

### Bloc 2 — DESIGN.md (à écrire dans `{safe_dirname(name)}/DESIGN.md` via `$impeccable document`)

> Si le skill `$impeccable` n'est pas disponible dans ton environnement : écrire DESIGN.md
> manuellement en respectant la structure ci-dessous. Le contenu reste le même, c'est juste
> la façon de le générer qui change.

Contenu requis :
- **Tokens couleur OKLCH** : primary, secondary, accent, bg, surface, text-main, text-muted, border, success, error
- **Échelle typographique** : display, h1, h2, h3, body-lg, body, body-sm, caption — taille / graisse / interligne / tracking — ratio ≥ 1.25 entre niveaux
- **Espacement** : progression logique, rythme variable par type de section
- **Patterns composants** : boutons (default/hover/active/focus/disabled), cards, badges, inputs, liens

Validation post-génération :
- [ ] Palette issue des photos (pas de bleu #3B82F6 par défaut)
- [ ] Aucune police bannie
- [ ] display ≥ 72 px, h1 ≥ 48 px, body = 16-18 px
- [ ] Stratégie couleur du CONCEPT respectée dans les surfaces

### Bloc 3 — ARCHITECTURE LAYOUT (décisions par section, anti-template)

```
ARCHITECTURE LAYOUT
────────────────────────────────────────────────────────
Hero      : Full-bleed photo | Split 60/40 | Color field + photo inset | Vidéo bg | Text dominant
            INTERDIT par défaut : hero centré texte blanc sur overlay sombre (pattern usé)
Services  : Liste éditoriale numérotée | Grille asymétrique 2+1 | Rangées alternées | Liste icônes
            INTERDIT : 3 colonnes égales icône+titre+2lignes (template WordPress)
Avis      : Inline dans le flux | Section dédiée carousel | Citations visuelles géantes
            RÈGLE : jamais uniquement en bas de page (avant section contact obligatoire)
Barre mobile : OUI sticky bottom bar (non-négociable)
Formulaire   : OUI mid-page après les avis (non-négociable)
Téléphone    : 3 fois minimum (header + hero + footer)
Micro-CTA    : 1 par section minimum
Rythme       : jamais 3 sections denses consécutives. Ex :
               Header(D) → Hero(A) → TrustBar(D) → Services(D/A) → PhotoBreak(A)
               → Avis(D) → Formulaire(A) → ZoneInter(D) → Footer(D)
────────────────────────────────────────────────────────
```

> ⚠️ STOP — les 3 blocs ci-dessus doivent être écrits dans ton message AVANT PHASE 3.
> Pas de "à voir" ou "selon contexte" — trancher sur la base des données disponibles.

### Bloc 4 — CINÉMATOGRAPHIE DU SCROLL (transitions entre fonds de sections)

Le scroll d'une page n'est pas une liste, c'est une progression narrative. Les transitions
entre fonds de sections créent des "scènes" — chaque section a sa propre atmosphère.

**Le défaut à éviter** : alterner brutalement `#FFFFFF → #F3F4F6 → #FFFFFF → #1F2937`.
Le visiteur ressent les coupures sans contexte narratif.

**3 approches possibles — à combiner avec modération** :

#### Approche A — Variations subtiles de fond (passe partout, à utiliser par défaut)

Au lieu de blanc/gris/blanc/sombre franc, faire des nuances très légères entre sections claires :
`#FBFAF6 → #F5F4F0 → #FBFAF6 → #1B2547`. La différence entre les nuances claires est de
**3-5% de luminance** (presque imperceptible consciemment, mais ressentie).

Bénéfice : la section sombre ponctuelle (formulaire ou trust bar) devient **beaucoup plus
impactante** quand elle arrive — le contraste est préservé pour le moment qui compte.

```css
/* Tokens à ajouter dans DESIGN.md, adapter teinte selon palette du projet */
--color-bg:           oklch(0.98 0.005 90);   /* #FBFAF6 — fond principal */
--color-surface:      oklch(0.96 0.005 90);   /* #F5F4F0 — sections alternees, -3% luminance */
--color-surface-dark: oklch(0.22 0.04 250);   /* #1B2547 — sections sombres ponctuelles */
```

#### Approche B — Overlap de contenu (à utiliser sur les moments forts)

Un élément d'une section (badge note, photo, card) dépasse légèrement dans la section suivante.
Crée une continuité visuelle sans avoir besoin de forme géométrique de transition.

```html
<!-- Exemple : badge note Google qui chevauche hero/trust bar -->
<section class="hero relative pb-24"> <!-- pb-24 pour laisser place au badge -->
  ...
</section>
<section class="trust-bar relative -mt-12 pt-20"> <!-- -mt-12 pour overlap -->
  <div class="absolute -top-8 left-1/2 -translate-x-1/2 z-10 bg-white shadow-xl rounded-2xl px-6 py-4">
    <!-- Badge note Google flottant entre les 2 sections -->
    <span class="js-review-rating font-bold">4.8</span>/5 · <span class="js-review-count">156</span> avis
  </div>
  ...
</section>
```

Bénéfice : crée un point fort visuel qui fait le pont entre 2 sections. Le badge **appartient
aux deux**, ancrant la trust bar dans la continuité du hero.

Limite : utiliser SEULEMENT sur 1-2 transitions par page (pas systématiquement).

#### Approche C — SVG wave/angle (à utiliser SI ça matche la géométrie du design)

Une forme SVG (vague, angle, ligne brisée) entre 2 sections. Souvent mal exécuté → effet
"template WordPress 2018".

**Règle stricte** : la forme DOIT matcher la géométrie générale du design :
- Angles droits / lignes brisées → site technique, industriel, BTP
- Vagues organiques → site bien-être, paysagisme, jardins
- ❌ Wave SVG générique sur un site plombier = anti-pattern

```html
<!-- Exemple angle minimal pour transition vers section sombre (site BTP/technique) -->
<section class="bg-bg py-20"> ... </section>
<div class="relative h-0">
  <svg class="absolute inset-x-0 -bottom-px w-full h-12 text-surface-dark" preserveAspectRatio="none" viewBox="0 0 1200 48">
    <polygon points="0,48 1200,0 1200,48" fill="currentColor"/>
  </svg>
</div>
<section class="bg-surface-dark py-20"> ... </section>
```

### Pattern rythme recommandé pour artisan (9 sections type)

```
Header       : transparent ou bg-surface très léger
Hero         : photo background OR bg principal
[overlap badge note Google entre hero et trust bar]  ← APPROCHE B
Trust bar    : bg-surface (-3% luminance)
Services     : bg-bg (retour au principal)
Photo break  : photo pleine largeur (rupture visuelle naturelle)
Avis         : bg-surface (rappel trust bar, cohérence)
FAQ          : bg-bg
[approche C optionnelle ici : angle SVG vers section sombre si géométrie cohérente]
Formulaire   : bg-surface-dark (LE moment de tension — contraste maximal)
Zone inter   : bg-bg (apaisement après le sombre)
Footer       : bg-surface-dark (clôture)
```

### Règles strictes

- **Maximum 4 nuances claires** dans toute la page (pas 8).
- **Une seule section sombre** par page principale — sinon le contraste perd son impact.
- **Différence entre nuances claires : 3-5% de luminance**. Pas plus (trop visible),
  pas moins (invisible donc inutile).
- **Header** : peut être transparent au-dessus du hero, puis bg-surface au scroll
  (transition CSS sur scroll).
- **Overlap (approche B)** : 1-2 fois par page MAX.
- **SVG (approche C)** : seulement si forme cohérente avec le design global.

### ❌ Anti-patterns à éviter

- 8 fonds différents sur une page → fragmentation visuelle, perd la narration
- Wave SVG générique entre TOUTES les sections → effet template WordPress
- Section sombre répétée (formulaire ET footer ET zone intervention sombre) → contraste dilué
- Fond `#FFFFFF` blanc pur → trop clinique, JAMAIS sur un site artisan


## 5. AVIS CLIENTS — TROIS FICHIERS DISTINCTS, NE PAS LES CONFONDRE

| Fichier | Localisation | Contenu | Usage |
|---|---|---|---|
| **`reviews_all.json`** | workspace (racine) | TOUS les avis bruts (100+) | **LIRE AU DÉMARRAGE** pour la voix client. Pas servi au runtime. |
| **`site/reviews.json`** | site/ | Top 12 avis (rating ≥4, texte > 30 chars) | Format frontend, déjà inliné dans reviews_inline.js |
| **`site/reviews_meta.json`** | site/ | `{{rating, count, place_id, review_url}}` | Compteur + URL de dépôt d'avis Google |
| **`site/reviews_inline.js`** | site/ | `window.SITE_REVIEWS` + `window.SITE_REVIEWS_META` | **À UTILISER** dans les composants, pas de fetch |

### Pourquoi reviews_inline.js plutôt que fetch('reviews.json') ?

- **`fetch()` est bloqué en `file://`** (CORS) → site cassé si quelqu'un ouvre l'HTML direct
- **`fetch()` est async** → contenus above-the-fold vides pendant 200-500ms → mauvais screenshot QC
- **`reviews_inline.js` est synchrone, fonctionne partout** → contenu disponible immédiatement

### Pattern à utiliser dans CHAQUE page HTML

```html
<!-- AVANT les scripts de composants (Alpine, etc.) -->
<script src="reviews_inline.js"></script>
```

Puis dans les composants Alpine.js (TestimonialsColumns, SimpleReviewCarousel) :
```javascript
init() {{
  // Source primaire : window.SITE_REVIEWS (inline, instantane, file://-safe)
  this.reviews = window.SITE_REVIEWS || [];
}}
```

### Compteurs DOM auto-sync

`reviews_inline.js` met à jour automatiquement à `DOMContentLoaded` :
- `.js-review-count`   → `window.SITE_REVIEWS_META.count` (ex: 208)
- `.js-review-rating`  → `window.SITE_REVIEWS_META.rating` (ex: 4.9)
- `a.js-review-url`    → `window.SITE_REVIEWS_META.review_url`

Donc pas besoin de script `syncReviewsMeta()` séparé. Juste inclure `reviews_inline.js`.

### Cohérence — règle absolue

Le NumberTicker des avis (data-target) DOIT utiliser **`window.SITE_REVIEWS_META.count`** (le TOTAL Google),
**PAS `reviews.json.length`** (qui vaut 12, le nombre affiché).

### Action concrète AVANT d'écrire le hero

Lire `reviews_all.json` (à la racine du workspace), repérer les 3-5 mots/expressions
qui reviennent le plus souvent dans la bouche des clients, et les réutiliser littéralement
dans le copy du hero, de la trust bar et des CTA. Beaucoup plus puissant que des promesses
génériques type "Qualité et professionnalisme".

### Top """
    doc += f"{len(top_reviews)} avis sélectionnés pour intégration prioritaire\n"
    if top_reviews:
        for i, rev in enumerate(top_reviews, 1):
            doc += f"""
**[{i}] {rev.get("author", "Client")}** — {rev.get("rating", 5)}/5 — {rev.get("date", "")}
> "{rev.get("text", "")[:400]}"
"""
    else:
        doc += "\n_Aucun avis texte disponible — utiliser les données de reviews.json_\n"

    doc += f"""
### Affichage avis
- Afficher la note globale **{rating}/5** avec **{n_rev} avis** en badge de confiance
- Masquer les avis sans texte (rating seul)
- Maximum 6 avis visibles simultanément — scroll horizontal sur mobile

---

## 5bis. PROTOCOLE ANTI-HALLUCINATION (NON NÉGOCIABLE)

> 🚨 **30-40 % des sites générés sans ce protocole contiennent du contenu inventé** :
> dates fausses, prix imaginaires, employés fictifs, garanties non-tenues, certifications usurpées.
> Chacune de ces inventions expose ton client à des sanctions légales (cf §0).
> Lis ce protocole en entier avant d'écrire la première ligne de copy.

### Sources autorisées (la vérité brute, à toi de l'analyser)

Il n'y a PAS de pré-analyse pré-mâchée. Le script Python a collecté les données brutes ; **c'est à toi
de les lire et d'en extraire mentalement ce qui est utile**. Les sources, par ordre de fiabilité :

1. **`reviews_meta.json`** — note, nombre d'avis, URL Google (source officielle scrapée)
2. **`profil.json`** — nom, adresse, téléphone, horaires, types, editorial_summary Google
3. **`reviews_all.json`** — **TOUS les avis bruts dédupliqués** (100+ potentiels)

**Si un fait spécifique n'est PAS dans ces 3 sources → il est INTERDIT de l'écrire.**

### Action obligatoire AVANT d'écrire la première ligne de copy

**Lire `reviews_all.json` en entier** et en extraire mentalement :
- Les **prénoms d'employés** cités par les clients (note les noms qui reviennent ≥ 2 fois)
- Les **services explicitement nommés** (PAC, salle de bain, chaudière, etc.)
- Les **qualités récurrentes** dans le vocabulaire client (rapide, propre, sérieux, à l'écoute, etc.)
- Les **mentions de garantie/assurance/décennale** (verbatim — si zéro mention, INTERDIT d'en écrire)
- Les **mentions de prix/tarif/devis** (verbatim — si zéro mention, INTERDIT d'inventer)
- Les **mentions d'ancienneté/dates** (verbatim — si zéro, INTERDIT d'inventer une date)
- Les **verbatims forts** réutilisables en citation

Comme pour les photos : **tu lis le matériau brut et tu décides**. Le script ne pré-classe rien.

### Les 3 modes de rédaction autorisés

#### Mode 1 — Précis et sourcé (privilégié)
Tout fait spécifique (chiffre, date, nom, garantie, prix, durée) vient d'une **source explicite**
ou d'une **citation littérale d'avis**.

Exemples valides :
- ✓ "208 avis Google, note moyenne 4,9/5" (source : reviews_meta.json)
- ✓ "Yoann et son équipe interviennent rapidement" (si "Yoann" cité dans plusieurs avis de reviews_all.json)
- ✓ "« Intervention le jour même, plombier sérieux. » — avis Google Marie L." (citation littérale)

#### Mode 2 — Générique métier honnête (quand pas de donnée précise)
Phrase **vraie pour toute entreprise du secteur**, ne ment pas, ne promet rien de spécifique, mais reste vendeuse.

Banque de formulations type :
- **Prix** : "Devis détaillé après visite — gratuit et sans engagement."
- **Délai** : "Planning communiqué dans le devis selon la complexité du chantier."
- **Garantie** : "Garanties applicables précisées dans chaque devis."
- **Expérience** : "Une approche artisanale soignée." (sans mention d'années)
- **Équipe** : "L'artisan vous accompagne de A à Z." / "Notre équipe à votre écoute."
- **Certifications** : ne rien mentionner si pas sourcé. Pas de "membre de RGE" inventé.

#### Mode 3 — Placeholder explicite (mentions légales UNIQUEMENT)
Sur la page mentions-legales.html, écrire littéralement `[À compléter]` pour les champs inconnus.
C'est attendu et professionnel sur cette page, jamais ailleurs.
Exemple : `SIRET : [À compléter par le client]`

#### Mode INTERDIT — Invention plausible
- ❌ Inventer une date de création ("depuis 1995", "fondée en 2008")
- ❌ Inventer un prénom d'employé ("Bruno chef d'équipe", "Marc commercial")
- ❌ Inventer un prix ("à partir de 25 000 €", "forfait dès 89 €")
- ❌ Inventer une garantie ("décennale 10 ans", "30 jours satisfait remboursé")
- ❌ Inventer une statistique ("98 % de clients satisfaits", "500 chantiers réalisés")
- ❌ Inventer une certification ("RGE", "Qualibat", "Qualifelec") sans preuve
- ❌ Signer un texte du nom du dirigeant sans qu'il l'ait approuvé

### Seuils de fiabilité par nombre de mentions

Sur 100+ avis, **1 mention isolée ≠ règle de l'entreprise**. Échelle :

| Nb mentions du même fait dans reviews_all.json | Statut | Action |
|---|---|---|
| 0 mention | **ABSENT** | Mode 2 générique ou suppression. JAMAIS l'affirmer. |
| 1 mention isolée | **EXCEPTION** | ⚠️ NE PAS en faire une règle de l'entreprise. Mode 2 générique. Si vraiment marquant, citation littérale entre guillemets avec attribution explicite à l'auteur ("Marie L. souligne : ..."). |
| 2-3 mentions | **PROBABLE** | OK pour Mode 1 sans citation (ex: nom d'employé Mehdi cité 3 fois → bloc équipe OK). |
| ≥ 4 mentions | **VÉRIFIÉ** | Mode 1 affirmé sans hésitation. |

Exemple concret : si **1 seul** avis mentionne "garantie 2 ans offerte par Karim" :
- ❌ Ne pas écrire "Garantie 2 ans systématique" dans la trust bar (faux : c'est un cas)
- ❌ Ne pas créer une section "Nos garanties" basée sur ce verbatim unique
- ✅ Acceptable : citation littérale entre guillemets avec attribution si on a vraiment besoin
- ✅ Sinon : Mode 2 "Garanties précisées dans chaque devis"

Cette règle s'applique à : garanties, prix, durées de chantier, statistiques, certifications.
Pour les noms d'équipe : seuil bas (2+ mentions suffisent — cohérent avec §7 PAGE 4).

### Sections SUPPRIMABLES (3 niveaux de réaction au manque de donnée)

**Niveau 1 — Phrase manquante** → utiliser Mode 2 (générique honnête).
**Niveau 2 — Section structurelle sans donnée** → **SUPPRIMER la section** entière, rééquilibrer le layout.
**Niveau 3 — Mentions légales** → Mode 3 (placeholder explicite).

Liste des sections à SUPPRIMER si données manquantes (à toi d'évaluer après lecture de reviews_all.json) :
| Section | Critère de présence | Si manquant |
|---|---|---|
| Timeline historique ("1995 / 2005 / 2015") | Dates précises dans `profil.json.editorial_summary` ou avis | SUPPRIMER. Pas de timeline inventée. |
| Équipe nominale ("Bruno / Marie / Paul") | Prénoms cités ≥ 2 fois dans reviews_all.json | SUPPRIMER section nominale. Parler de "l'artisan" / "l'équipe" en générique. |
| Formules tarifaires ("Saison / Confort / Premium") | Tarifs publiquement annoncés (ancien site) | **TOUJOURS SUPPRIMER** sauf si vraies formules connues. JAMAIS d'invention de gamme. |
| Statistiques chiffrées ("500 clients / 15 ans / 98 %") | Source explicite verbatim | SUPPRIMER. Pas de pourcentage / chiffre rond inventé. |
| Certifications nominales (RGE, Qualibat, Qualifelec) | Mention littérale dans données | SUPPRIMER. Pas d'organisme cité sans preuve. |
| Histoire / "Notre parcours" | `editorial_summary` Google riche OU mentions ancienneté précises | SUPPRIMER. Remplacer par "Notre approche" (présent uniquement). |

### Workflow obligatoire AVANT d'écrire chaque section

1. Identifier les **données requises** pour cette section (cf. §7 specs)
2. Vérifier leur présence dans les sources brutes (reviews_all.json en premier, profil.json ensuite)
3. **Si présentes** → Mode 1 (sourcé, citer si pertinent)
4. **Si partiellement présentes** → Mode 1 pour ce qui est sourcé, Mode 2 pour les phrases descriptives
5. **Si totalement absentes** → SUPPRIMER la section ou Mode 2 si phrase générique suffit

### Validation post-build OBLIGATOIRE

Le script `audit_content.py` (déjà présent dans ce workspace) scanne le HTML généré et produit
`HALLUCINATIONS_REPORT.md`. **Sans rapport vert (0 ❌) : interdiction de passer en PHASE 6 deploy.**
Cf. PHASE 5 (§6) pour le détail du workflow d'audit.

---

## 6. SÉQUENCE DE BUILD — 7 PHASES

### PHASE 1 — Acquisition + Décisions design + Décisions composants

> ⚠️ **L'ordre est crucial.** Ne pas décider de la palette ou des composants AVANT d'avoir vu
> les photos et lu les avis. Sinon on construit un concept dans le vide qui devra être
> rétro-ajusté quand le matériau réel apparaîtra.

#### Étape 1.1 — Inventaire ressources

1. **Lire COMPONENTS.md** (inventaire des **10 composants** — pas encore de décision OUI/NON)
2. `ls photos/` pour voir combien de fichiers réels
3. `ls videos/` (peut être vide)

#### Étape 1.2 — Analyser les photos (§4 gate bloquant)

4. Analyser visuellement CHAQUE photo dans `site/photos/` (cf §4 protocole médias)
5. Écrire `site/photos/manifest.json` avec une entrée par fichier
6. Compteur visible : `Photos analysées : N/N ✓`

#### Étape 1.3 — Lire les avis (§5bis)

7. Lire `reviews_all.json` en entier
8. Repérer mentalement : prénoms équipe récurrents, services nommés, qualités récurrentes,
   mentions de garantie/prix/tenure, verbatims puissants

#### Étape 1.4 — Décisions design éclairées par le matériau (§4ter — 4 blocs)

9. Compléter le bloc **CONCEPT DESIGN** (phrase-concept, stratégie couleur, direction typo, scène, thème)
   → palette tirée des VRAIES couleurs des photos, pas de cliché métier
10. Générer **DESIGN.md** (`$impeccable document` — fallback manuel si skill indisponible)
11. Compléter le bloc **ARCHITECTURE LAYOUT** (hero, services, avis, rythme)
12. Compléter le bloc **CINÉMATOGRAPHIE DU SCROLL** (transitions entre fonds de sections — cf §4ter Bloc 4)

#### Étape 1.5 — Décisions composants (maintenant toutes les données sont là)

13. Documenter le bloc **DÉCISION COMPOSANTS** ci-dessous (les 10 composants disponibles)

```
DÉCISION COMPOSANTS
─────────────────────────────────────────────────────────
NumberTicker          : OUI/NON — [chiffres clés à animer : avis Google ? années ?]
TextHighlighter       : OUI/NON — [direction typo Éditoriale ? mots-clés à souligner ?]
ImageGallery          : OUI (realisations.html) — toujours
BeforeAfterSlider     : OUI/NON — paires avant/après dans manifest.json : [liste ou "aucune"]
NavigationMenuDesktop : OUI/NON — nombre de services distincts identifiés : [X]
TestimonialsColumns   : OUI/NON — avis texte dans reviews_all.json : [X / seuil 8]
SimpleReviewCarousel  : OUI/NON — alternative si TestimonialsColumns NON
FAQAccordion          : OUI/NON — nombre total avis dans reviews_all.json : [X / seuil 30]
StaggerReveal         : OUI/NON — sections où l'appliquer (MAX 2/page) : [liste ou "aucune"]
CursorAwareShadow     : OUI/NON — appliqué uniquement sur Service cards homepage si OUI
─────────────────────────────────────────────────────────
```

> ⚠️ STOP — chaque ligne doit être tranchée OUI/NON avec justification chiffrée ou nommée.
> Pas de "à voir" : tu as toutes les données maintenant (photos, avis, layout décidé).

> ⚠️ Ne pas ouvrir PHASE 3 sans ces 6 outputs documentés :
> manifest.json + CONCEPT DESIGN + DESIGN.md + ARCHITECTURE LAYOUT + CINÉMATOGRAPHIE SCROLL + DÉCISION COMPOSANTS.

### PHASE 2 — Architecture fichiers (séparation workspace / site/)

> ⚠️ **CRITIQUE** : tous les fichiers `.html` que tu crées vont dans le sous-dossier `site/`,
> **JAMAIS à la racine du workspace**. Le workspace contient CLAUDE.md et tes outils ;
> `site/` contient ce qui sera déployé sur Vercel.

```
{safe_dirname(name)}/                    ← WORKSPACE (Claude Code travaille ici)
├── CLAUDE.md                            ← ce fichier (conducteur)
├── COMPONENTS.md                        ← bibliothèque composants vanilla
├── PRODUCT.md                           ← contexte métier
├── DESIGN.md                            ← À CRÉER par toi en PHASE 1
├── reviews_all.json                     ← lecture initiale (voix client §5)
├── profil.json, profil.txt              ← données brutes scraper
├── preview_site.py                      ← outil capture screenshots
└── site/                                ← DEPLOYABLE (cd site && /deploy)
    ├── index.html                       ← À CRÉER par toi
    ├── services.html                    ← À CRÉER par toi
    ├── realisations.html                ← À CRÉER par toi
    ├── a-propos.html                    ← À CRÉER par toi
    ├── contact.html                     ← À CRÉER par toi
    ├── merci.html                       ← À CRÉER par toi (confirmation form)
    ├── mentions-legales.html            ← À CRÉER par toi
    ├── politique-confidentialite.html   ← À CRÉER par toi
    ├── favicon.svg                      ← déjà présent
    ├── sitemap.xml, robots.txt          ← déjà présents
    ├── reviews.json                     ← top 12 avis (déjà présent)
    ├── reviews_meta.json                ← compteur (déjà présent)
    ├── reviews_inline.js                ← embed JS des avis (déjà présent, voir §5)
    ├── photos/                          ← déjà présent (+ manifest.json à créer §4)
    └── videos/                          ← déjà présent (vide si pas de vidéos)
```

**Référence aux assets depuis les HTML** : les `.html` étant dans `site/`, les chemins relatifs
sont : `<img src="photos/photo_01.jpg">`, `<script src="reviews_inline.js">`, `<link rel="icon" href="favicon.svg">`.
**Jamais** `../photos/` — tout est au même niveau que les HTML.

### PHASE 3 — Build HTML (skill: impeccable → craft)
Construire chaque page en suivant STRICTEMENT :
- Le brief ARCHITECTURE LAYOUT de §4ter (hero, services, avis, rythme) — jamais dévier sans justification
- Le DESIGN.md généré en Phase 1 (tokens couleur, échelle typo, patterns composants)
- La structure de §7 (specs par page)
- Le stack technique de §8
- Les components obligatoires de §9

### PHASE 4 — Polish interactions + Desktop review (skill: impeccable → polish + emil-design-eng)
- Appliquer les animations `emil-design-eng` sur tous les éléments interactifs
- Vérifier le widget horaires temps-réel (§3)
- Vérifier les micro-interactions (hover, press, reveal)
- Tester le formulaire FormSubmit.co (penser à confirmer l'email à la 1ère soumission)

**Desktop Quality Gate — vérification obligatoire à chaque breakpoint :**
Après le polish mobile, ouvrir mentalement (ou via DevTools) le site à 768px, 1024px, 1280px et 1440px et valider :
- [ ] Aucun texte ne dépasse 72ch de longueur de ligne sur desktop (`max-w-prose` ou `max-w-2xl` sur les blocs de corps)
- [ ] Le hero ne s'étire pas de façon disgracieuse sur grand écran — hauteur min définie (`lg:min-h-[700px]` ou `lg:h-screen`)
- [ ] Les grilles passent correctement de 1 colonne (mobile) aux colonnes prévues en §4ter (`md:grid-cols-2 lg:grid-cols-3`)
- [ ] La barre sticky mobile (`md:hidden`) ne s'affiche PAS sur desktop — vérifier l'attribut de visibilité
- [ ] Les titres `h1` / `h2` ont un scale responsive (`text-4xl lg:text-6xl`) — pas la même taille mobile/desktop
- [ ] Les sections ont un padding vertical généreux sur desktop (`py-16 lg:py-24`) — le mobile-tight ne suffit pas
- [ ] Les images `object-cover` sont bien cadrées sur les ratios desktop (16:9 ou 4:3) — pas coupées de façon étrange
- [ ] Le formulaire de contact passe en 2 colonnes sur desktop (`md:grid-cols-2`) pour les champs courts
- [ ] Le `max-w-7xl mx-auto` (ou équivalent) est appliqué sur tous les conteneurs — jamais de contenu pleine largeur sans contrainte sur ≥ 1280px
- [ ] La navigation desktop (top bar) est propre, visible et fonctionnelle — pas écrasée par le burger mobile

### PHASE 4.5 — REGARD (gate visuel obligatoire avec preview_site.py)

> ⚠️ **Cette phase a un outil concret : `preview_site.py`. Pas d'excuse pour la sauter.**
>
> Tu ne peux pas livrer un site sans l'avoir vraiment regardé. Le script `preview_site.py`
> (déjà dans ce dossier) lance un serveur local, prend des screenshots de chaque page en
> mobile (390×844) et desktop (1440×900), et les sauve dans `preview/`. Tu DOIS ensuite
> les lire avec le tool `Read` et juger.

**Procédure obligatoire** :

1. **Exécuter** : `python preview_site.py` (depuis la racine de ce dossier)
   → génère `preview/index_mobile.png`, `preview/index_desktop.png`, etc. pour chaque page
2. **Lire chaque image** avec le tool `Read` (il supporte les PNG nativement)
3. **Pour chaque page, pour chaque section visible**, te poser :
   > *"Si je voyais ça pour la première fois, est-ce que ça a l'air CHER, NORMAL, ou CHEAP ?"*
4. **Pour chaque section NORMAL ou CHEAP** → écrire littéralement ce qui sonne faux. Exemples :
   - "le badge Google flotte tout seul à droite du hero, ça fait pièce rapportée"
   - "la headline mobile est cassée sur 3 lignes alors qu'il y a de la place"
   - "le marquee va trop vite, on dirait un site de promo discount"
   - "le gap au-dessus du hero est trop serré, ça étouffe"
   - "les coins des cards ne sont pas alignés entre eux"
5. **Corriger** chaque point identifié dans le HTML/CSS
6. **Re-lancer** `python preview_site.py` et re-juger
7. **Ne passer à PHASE 5** que quand TOUTES les pages, TOUTES les sections, sont en "CHER"

#### Points de vigilance fréquents (à scruter en priorité)

- **Espacements top de page** : le hero respire-t-il ? Pas écrasé contre le header ?
- **Largeur des textes mobile** : occupent-ils la largeur disponible ? Pas de wrap forcé bizarre ?
- **Éléments orphelins** : un badge, une note, une icône isolée sur le côté → réintégrer dans un bloc
- **Vitesses d'animation** : marquees ≥ 60s par boucle, transitions ≥ 250ms, jamais brutal
- **Alignements** : tous les coins de cards alignés, toutes les baselines de texte cohérentes
- **Photos** : aucune étirée, aucune coupée maladroitement, ratios cohérents par section
- **Densité visuelle** : aucune zone de vide qui fait "pas fini", aucune zone surchargée

**Si `preview_site.py` échoue** (Playwright manquant, port occupé) : lancer
`pip install playwright && playwright install chromium`, puis réessayer.
Sans screenshots, cette phase n'a aucune valeur — corriger l'environnement avant de continuer.

### PHASE 5 — Audit qualité (skill: impeccable → audit)

**Étape 5.A — Audit contenu obligatoire (anti-hallucination)** :
1. Exécuter : `python audit_content.py` depuis la racine du workspace
2. Lire `HALLUCINATIONS_REPORT.md` qui sort
3. Pour chaque **[X] hallucination probable** :
   - Soit **supprimer** le fait du HTML
   - Soit **citer la vraie source** (avis Google verbatim, profil.json)
   - Soit **passer en Mode 2** (formulation générique honnête — cf §5bis)
4. Pour chaque **[?] ambiguïté** : vérifier le contexte, ajuster si suspect
5. **Re-lancer `python audit_content.py`** jusqu'à atteindre **0 [X]**
6. ⚠️ **Sans rapport vert, INTERDICTION de passer en PHASE 6 deploy.**

**Étape 5.B — Audit qualité standard** :
- Passer le checklist conversion de §10
- Vérifier le JSON-LD de §11
- Vérifier la navigation mobile bottom bar
- Confirmer zéro "Never Do" de §12

### PHASE 6 — Deploy
```
/deploy
```
Vercel. Pas de build tools. Upload statique direct.

---

## 7. SPECS PAR PAGE

### PAGE 1 — index.html (Homepage)

**Section 1 : Header sticky**
- Logo : vérifier `photos/logo.png` en priorité
  - **Si présent** : `<img src="photos/logo.png" alt="Logo {name}" class="h-10 w-auto object-contain">` — ajouter `class="brightness-0 invert"` sur fond sombre
  - **Si absent** : nom de l'entreprise en texte, font display, couleur de marque
- Téléphone `{phone}` cliquable `tel:` au centre ou droite — toujours visible
- Menu desktop : Accueil | Services | Réalisations | À Propos | Contact
- Mobile : header réduit avec logo + téléphone + burger → navigation bottom bar (cf. §9)

**Section 2 : Hero**

🎯 **LE HEADLINE EST LA PHRASE LA PLUS IMPORTANTE DU SITE.** Ne pas la bâcler en générique.

Méthode : utiliser CONCRÈTEMENT la VoC (reviews_all.json) — prénom de l'artisan cité,
service spécifique récurrent, délai mentionné, zone réelle d'intervention.

❌ **Mauvais headlines (génériques, oubliables)** :
- "Plombier à Reims — Devis gratuit"
- "Votre artisan de confiance"
- "Qualité et professionnalisme depuis toujours"
- "{metier_label} à {ville}"

✅ **Bons headlines (sourcés, mémorables)** :
- "Karim intervient le jour même sur toutes vos fuites à Reims" (utilise prénom voc + service voc + délai voc + zone profil)
- "208 voisins l'ont appelé pour leur chaudière. Et vous ?" (utilise nb avis + service voc, conversationnel)
- "Vos canalisations méritent un plombier qui répond." (positionnement vs concurrence implicite)
- "Le plombier que Reims appelle quand ça presse." (utilise zone + insight VoC sur urgence)

Sous-titre : bénéfice concret extrait des avis (jamais "satisfaction garantie" générique).
- ❌ "Une équipe d'experts à votre service"
- ✅ "Diagnostic en 15 minutes, intervention dans la journée — {ville} et 30 km autour."

- CTA principal : "Demander un devis gratuit" (→ #contact ou contact.html)
- CTA secondaire : "Appeler le {phone}" (tel: link)
- Background : meilleure photo chantier en `background-image` + overlay sombre 50%
- Badge flottant : `{rating}⭐ · {n_rev} avis Google`

**Section 3 : Barre de confiance (trust bar)**
- 3 à 4 chips horizontaux. Chips AUTORISÉS uniquement si sourcés :
  - ✓ `{n_rev} avis vérifiés` (source : reviews_meta.json) — TOUJOURS OK
  - ✓ `Artisan local {ville}` (source : profil.json adresse) — TOUJOURS OK
  - ✓ `Devis gratuit` (Mode 2 générique — OK)
  - ✓ `Réponse rapide` (Mode 2) ou citation littérale si tu vois des mentions de rapidité dans reviews_all.json
- ❌ JAMAIS de chip avec chiffre inventé ("15 ans d'expérience", "500 clients", "98 % satisfaits")
  si non présent littéralement dans `reviews_all.json` ou `profil.json`
- Fond légèrement contrasté — jamais identique au hero

**Section 4 : Services (cards)**
- 2 à 4 cartes minimum
- **Source des services** (par ordre de priorité) :
  1. Services explicitement cités dans `reviews_all.json` (à toi de lire les avis et repérer)
  2. `profil.json.types` (types Google : plumber → "Plomberie", etc.)
  3. Si rien : services génériques larges (ne PAS inventer de spécialités)
- ❌ JAMAIS de service très spécifique non sourcé ("Installation domotique Niko", "Spa scandinave")
- Chaque carte : icône Material Symbols + titre service + description 1-2 lignes
- Layout : 2 colonnes sur mobile, 3-4 sur desktop — **jamais 3 colonnes égales** (anti-pattern)

🎯 **Description de chaque service : INSPIRÉE DES VRAIS AVIS, pas générique.**

Méthode : pour chaque service, retrouver dans `reviews_all.json` les avis qui mentionnent
ce service. Identifier les cas concrets décrits par les clients (ex: "Karim a réparé ma
chasse d'eau qui fuyait depuis 2 mois", "Mehdi a remplacé mon ballon en 1 matinée").
Reformuler en description de service (pas une citation entre guillemets, une vraie phrase
descriptive) qui évoque les cas réels.

❌ **Descriptions génériques (à éviter)** :
- "Intervention rapide et soignée pour vos fuites et dépannages plomberie."
- "Notre équipe d'experts à votre service 7j/7."
- "Solutions sur mesure adaptées à votre besoin."

✅ **Descriptions sourcées (à viser)** :
- "Fuite de robinet, WC qui fuit, chasse d'eau, ballon d'eau chaude qui lâche — diagnostic
  en 15 minutes et intervention dans la journée."
- "Remplacement de chaudière gaz ou installation neuve, raccordement aux normes,
  mise en service le jour même."
- "Salle de bain refaite de A à Z : douche italienne, vasque, robinetterie, faïence — chantier
  livré clé en main en 2-3 semaines."

Les phrases ci-dessus sont **vendeuses sans inventer**. Elles décrivent ce que les clients
ont VRAIMENT vécu (visible dans les avis), pas des promesses creuses.

- ❌ **JAMAIS de "Formules tarifaires" (Saison/Confort/Premium)** sans tarifs publiquement annoncés.
  Ce pattern de 3 cards tarifées est l'hallucination #1 du secteur — SUPPRIMER si pas de tarifs réels.

**Section 5 : Réalisations (aperçu)**
- 4 à 6 photos de chantiers en grille asymétrique
- Lien "Voir toutes les réalisations →" → realisations.html

**Section 6 : Avis Google**
- Badge note globale : grand, visible — `{rating}/5 · {n_rev} avis Google`
- Carousel horizontal d'avis (depuis reviews.json)
- Source : "Avis vérifiés Google Maps"

**Section 7 : FAQ (Questions fréquentes)**

🎯 **Section haute valeur SEO + conversion.** Les FAQ extraites des avis répondent aux
objections précises qui empêchent le visiteur d'appeler. Bonus : schema JSON-LD FAQPage =
opportunité de featured snippets Google.

**Décision** : INCLURE cette section sur index.html si `reviews_all.json` contient ≥ 30 avis
(matière suffisante). Si < 30 avis : skip (risque d'inventer).

**Composant à utiliser** : **FAQAccordion** dans COMPONENTS.md §8 (port vanilla du Radix Accordion).

**Méthode d'extraction des questions depuis reviews_all.json** :

Les questions ne s'inventent pas — elles s'extraient. Quand un client écrit *"il est arrivé
le 14 juillet à 6h30"*, il répond à *"Intervenez-vous les jours fériés ?"*. Quand il écrit
*"il a pris le temps de tout m'expliquer"*, il répond à *"Allez-vous prendre le temps de
m'expliquer ce qui s'est passé chez moi ?"*.

**Procédure en 3 étapes** :
1. **Lire les avis** en cherchant les peurs implicites et les éloges récurrents
2. **Regrouper par catégorie d'objection** :
   - **Réactivité / Timing** : délai, jours fériés, week-end, jour même
   - **Méthode / Expertise** : comment vous travaillez, est-ce destructif, diagnostic
   - **Tarif / Devis** : gratuité, transparence, assurance, paiement
   - **Zone d'intervention** : jusqu'où, déplacement, communes proches
3. **Formuler 5-8 questions max** qui répondent aux objections **précises** qui empêchent
   le visiteur d'appeler.

**Règle d'or pour chaque réponse** :
- Mode 1 (sourcé) : utiliser un fait verbatim des avis si possible
- Mode 2 (générique honnête) si pas de source
- **Finir par un micro-CTA naturel** : numéro de téléphone cliquable OU lien devis

❌ **Mauvais** : *"Oui nous intervenons rapidement."* (générique, sans engagement, sans CTA)

✅ **Bon** : *"Pour les urgences — fuite active, dégât des eaux — {{Prénom}} fait son maximum
pour intervenir le jour même sur {ville}. Appelez le **{phone}** pour vérifier sa disponibilité."*
(spécifique + prénom sourcé + cas concret + zone sourcée + micro-CTA cliquable)

⚠️ **Cohérence schema ↔ HTML obligatoire** : si tu utilises le FAQAccordion, inclure aussi
le bloc JSON-LD FAQPage dans `<head>` (cf §11 SEO). Les questions du schema DOIVENT être
textuellement présentes dans le HTML visible (Google vérifie).

**Section 8 : Zone d'intervention**
- Titre : "Nous intervenons sur {zone_str}"
- **Source des villes (par ordre de priorité)** :
  1. **Villes réellement citées dans `reviews_all.json`** — les clients mentionnent souvent
     leur ville ("intervention à Tinqueux", "j'habite Bétheny", "déplacement jusqu'à Cormontreuil").
     Ces villes sont le VRAI rayon d'action et donnent une crédibilité immédiate au visiteur
     qui voit sa propre ville listée.
  2. Si moins de 5 villes citées dans les avis, compléter avec les communes voisines géographiquement
     proches (10-20 km autour de {ville}).
- ❌ Ne pas inventer un rayon trop large (ex: "toute la région Grand Est") si les avis montrent
  un rayon réel de 15-20 km. Mentir sur la zone = perte de crédibilité.
- Optionnel : carte SVG ou embed Google Maps iframe

**Section 9 : Formulaire de contact (ancre #contact)**
- Champs : Prénom (required) | Téléphone (required) | Email | Type de prestation | Message
- Submit → FormSubmit.co (voir §8)
- Titre : "Devis gratuit & sans engagement"

**Section 10 : Footer**
- Adresse : {address}
- Téléphone : {phone}
- Horaires synthétiques
- Liens : Mentions légales | Politique de confidentialité
- Copyright © {datetime.now().year} {name}
{social_block}

---

### PAGE 2 — services.html

- Hero compact : "Nos Services" + sous-titre métier
- Grille détaillée de tous les services (6–10 minimum)
- Chaque service : icône + titre + description 3–5 lignes + bullet points des prestations incluses
- **Section FAQ spécifique aux services** (5-8 questions techniques) : utiliser FAQAccordion
  avec `name="faq-services"`. Questions différentes de la FAQ index (qui est généraliste).
  Exemples pour plomberie : *"Faut-il couper l'eau pendant l'intervention ?"* / *"Le diagnostic
  est-il payant ?"* / *"Avez-vous le matériel pour les fuites enterrées ?"*. Inclure aussi le
  bloc JSON-LD FAQPage correspondant (§11 SEO).
- CTA en fin de page → formulaire devis

---

### PAGE 3 — realisations.html

- Galerie masonry ou grille des photos de chantiers
- Filtre par catégorie si plusieurs types de travaux
- Chaque photo : alt text descriptif (SEO)
- CTA flottant ou sticky → "Demander un devis"

---

### PAGE 4 — a-propos.html

> ⚠️ **Page à HAUT RISQUE d'hallucination.** Lire §5bis avant d'écrire.
> Tout fait spécifique non sourcé = INTERDIT. Plutôt supprimer une sous-section que l'inventer.

**Bloc Histoire / Parcours**
- Données requises : `profil.json.editorial_summary` OU dates précises citées dans `reviews_all.json`
- Si absentes → **SUPPRIMER le bloc Histoire**. Remplacer par "Notre approche" (au PRÉSENT,
  sans date, sans timeline). Exemple Mode 2 : "Une approche artisanale soignée, chantier après chantier."
- ❌ JAMAIS de timeline "1995 / 2005 / 2015" si dates non sourcées
- ❌ JAMAIS de phrase "depuis X années" sans source

**Bloc Équipe**
- Données requises : prénoms cités ≥ 2 fois dans `reviews_all.json` (à toi de lire et compter)
- Si présents → mentionner les prénoms vraiment cités, avec leur rôle SI il apparaît dans les verbatims
  (ex: si plusieurs avis disent "Yoann m'a installé...", écrire "Yoann, plombier")
- Si absents → **SUPPRIMER le bloc Équipe nominale**. Parler de "l'artisan" ou "l'équipe" en générique.
- ❌ JAMAIS de "Bruno chef d'équipe Construction" inventé
- ❌ JAMAIS attribuer un rôle précis sans verbatim qui le confirme

**Bloc Certifications / Garanties**
- Données requises : mention littérale "garantie/décennale/assurance/RGE/Qualibat" dans `reviews_all.json` ou `profil.json`
- Si présentes → citer LITTÉRALEMENT le verbatim de l'avis ("Les clients soulignent : « ... »")
- Si absentes → **SUPPRIMER le bloc**. Pas de "Garantie décennale 10 ans", pas de "Membre RGE".
- Mode 2 acceptable : "Garanties applicables précisées dans chaque devis."

**Bloc Valeurs**
- Données requises : adjectifs qualité qui reviennent dans `reviews_all.json` (lire les avis, repérer les mots récurrents)
- Si présents → utiliser les vrais mots qui reviennent dans les avis (vocabulaire client réel)
- Si absents → Mode 2 : valeurs métier génériques honnêtes ("écoute", "soin", "fiabilité")
  sans chiffrer ("100 % engagement") et sans inventer.

**Bloc Avis (mini — 3 avis)**
- Source : `reviews.json` (top 3 verbatims)
- Toujours citer le nom de l'auteur + rating (transparence)

---

### PAGE 5 — contact.html

- Formulaire complet (mêmes champs que homepage)
- Embed Google Maps : `https://maps.google.com/maps?q={lat},{lng}&output=embed`
- Bloc horaires avec widget statut temps-réel (§3)
- Téléphone + adresse bien visibles

---

### PAGE 6 — mentions-legales.html & politique-confidentialite.html

{legal_block}

---

## 7bis. MICRO-COPY — la voix de la marque dans chaque mot

🎯 **Principe** : chaque texte que le visiteur lit est soit dans la voix de **ta marque**
(spécifique, humaine, sourcée), soit dans la voix d'un **template** (générique, oubliable).
Le LLM par défaut écrit en voix de template.

Ta mission sur **CHAQUE bouton, label, placeholder, alt text, message** : remplacer le
générique par le spécifique. C'est probablement l'amélioration la plus impactante sur la
perception de qualité — et la moins discutée.

### Méthode

Pour chaque texte, te demander :
1. **Est-ce que ce texte pourrait apparaître à l'identique sur 100 autres sites ?**
   → Si oui = générique = à réécrire.
2. **Utilise-t-il au moins UN élément concret** : prénom artisan (sourcé reviews_all), ville
   sourcée, durée sourcée, exemple précis ?
   → Si non = creux = à enrichir.

### Table de référence — 17 zones à auditer (template vs premium)

| Zone | ❌ Template (générique) | ✅ Premium (spécifique + humain) |
|---|---|---|
| **Bouton CTA principal** | "Envoyer ma demande" | "Demander mon devis gratuit →" |
| **Bouton CTA secondaire** | "Nous contacter" | "Appeler {{Prénom}} au {{tel}}" |
| **Placeholder champ message** | "Décrivez votre projet" | "Ex : fuite sous évier depuis 3 jours, {{ville}}" |
| **Label champ téléphone** | "Téléphone" | "Téléphone (où on peut vous joindre)" |
| **Trust bar — avis** | "{n_rev} avis vérifiés" | "{n_rev} voisins ont fait confiance à {{Prénom}}" |
| **Trust bar — disponibilité** | "Disponible 7j/7" | "{{Prénom}} répond entre 7h et 19h, 6j/7" |
| **Widget horaires (fermé)** | "Fermé · Ouvre lun. à 07:00" | "Fermé pour l'instant — {{Prénom}} reprend lundi à 7h" |
| **Widget horaires (ouvert)** | "Ouvert · Ferme à 18:00" | "{{Prénom}} est joignable jusqu'à 18h" |
| **Section CTA milieu page** | "Vous avez un projet ?" | "Une fuite, un doute, un projet ? {{Prénom}} répond." |
| **Footer copyright** | "© {datetime.now().year} {name}" | "© {datetime.now().year} {name} · Site fait avec attention à {{ville}}" |
| **Alt text photo hero** | "Photo {name}" | "{{Prénom}} et son véhicule devant une maison à {{ville}}" |
| **Alt text photo chantier** | "Salle de bain rénovée" | "Salle de bain refaite par {{Prénom}} pour un client de {{ville}}" |
| **Page merci.html — titre** | "Votre demande a été envoyée" | "{{Prénom}} a reçu votre demande" |
| **Page merci.html — sous-titre** | "Nous vous répondrons rapidement" | "Il vous rappelle généralement dans la journée" |
| **404.html (si présent)** | "Page introuvable" | "Cette page n'existe pas — mais {{Prénom}} oui : {{tel}}" |
| **Aria-label menu mobile** | "Menu de navigation" | "Menu — accéder aux services et au contact" |
| **Aria-label téléphone** | "Téléphone" | "Appeler {{Prénom}} maintenant" |

### Règles strictes

- **Prénom JAMAIS inventé** : si pas de prénom récurrent dans reviews_all.json (≥ 3 mentions),
  utiliser "l'artisan", "l'équipe", ou le nom de l'entreprise. JAMAIS inventer un prénom plausible.
- **Garder court** : un micro-copy spécifique ne doit pas devenir un paragraphe. Une phrase max.
- **Garder humain** : "Vous joindre" plutôt que "Prendre contact avec vous". "Il vous rappelle"
  plutôt que "Nous prendrons contact avec vous".
- **Pas d'emoji** sauf si le ton de la marque le justifie (artisan jeune, service express).
  Pour plomberie/électricité standard : pas d'emoji.
- **Tester chaque texte** : si tu l'imagines sur un site concurrent sans changement, il est
  générique. Réécrire.

---

## 8. STACK TECHNIQUE — EXACT, AUCUNE DÉVIATION

### Pattern image obligatoire — WebP + fallback JPG

Chaque photo téléchargée a déjà sa variante `.webp` à côté du `.jpg`. **Utiliser systématiquement
`<picture>` avec WebP en priorité** pour gagner 30-50% de poids :

```html
<picture>
  <source srcset="photos/photo_01.webp" type="image/webp">
  <img src="photos/photo_01.jpg" alt="Réalisation NOM_ENTREPRISE - VILLE"
       loading="lazy" width="800" height="600" class="...">
</picture>
```

Ne JAMAIS utiliser `<img src="photos/photo_X.jpg">` seul. Toujours `<picture>` avec source WebP.
Exception : la photo hero (au-dessus du fold) → `loading="eager"` au lieu de `lazy`.


```html
<!-- Favicon — TOUJOURS présent, jamais vide. favicon.svg est pré-généré. -->
<link rel="icon" type="image/svg+xml" href="favicon.svg">
<link rel="icon" type="image/png" href="favicon.svg" sizes="any">

<!-- Tailwind CSS CDN — pas de build, pas de purge -->
<script src="https://cdn.tailwindcss.com"></script>

<!-- Alpine.js — réactivité légère, widget horaires, carousel -->
<script defer src="https://cdn.jsdelivr.net/npm/alpinejs@3.x.x/dist/cdn.min.js"></script>

<!-- Material Symbols Outlined — icônes -->
<link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200" rel="stylesheet">

<!-- Google Fonts — à définir après analyse photos (stitch-design-taste) -->
<!-- Fonts : à valider après analyse photos via impeccable. Défaut : Bricolage Grotesque (titres) + Space Grotesk (corps). Bannir : Outfit, DM Sans, Inter, Plus Jakarta Sans, Instrument Sans -->
<link href="https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400;12..96,600;12..96,700;12..96,800&family=Space+Grotesk:wght@300;400;500;600;700&display=swap" rel="stylesheet">
```

### Configuration Tailwind (dans <script> after CDN)
```javascript
tailwind.config = {{
  theme: {{
    extend: {{
      colors: {{
        brand: {{
          // Remplir avec couleurs extraites des photos
          primary:   '#XXXXXX',  // Couleur principale de la marque
          secondary: '#XXXXXX',  // Couleur secondaire
          dark:      '#0F172A',  // Quasi-noir — jamais #000000
          light:     '#F8FAFC',  // Quasi-blanc fond
        }}
      }},
      fontFamily: {{
        display: ['Bricolage Grotesque', 'sans-serif'],
        body:    ['Space Grotesk', 'sans-serif'],
      }},
      animation: {{
        'fade-up':    'fadeUp 0.5s cubic-bezier(0.23, 1, 0.32, 1) both',
        'fade-in':    'fadeIn 0.4s cubic-bezier(0.23, 1, 0.32, 1) both',
        'slide-left': 'slideLeft 0.4s cubic-bezier(0.23, 1, 0.32, 1) both',
      }},
      keyframes: {{
        fadeUp:    {{ from: {{ opacity: '0', transform: 'translateY(20px)' }}, to: {{ opacity: '1', transform: 'translateY(0)' }} }},
        fadeIn:    {{ from: {{ opacity: '0' }}, to: {{ opacity: '1' }} }},
        slideLeft: {{ from: {{ opacity: '0', transform: 'translateX(20px)' }}, to: {{ opacity: '1', transform: 'translateX(0)' }} }},
      }}
    }}
  }}
}}
```

### FormSubmit.co (formulaire sans backend, sans compte)
```html
<form action="https://formsubmit.co/{formsubmit_email}" method="POST">
  <!-- Sujet de l'email reçu -->
  <input type="hidden" name="_subject" value="Nouveau devis — {name}">

  <!-- Redirect après envoi -->
  <input type="hidden" name="_next" value="__SITE_URL__/merci.html">

  <!-- Désactiver le captcha de FormSubmit -->
  <input type="hidden" name="_captcha" value="false">

  <!-- Format de l'email reçu : tableau lisible -->
  <input type="hidden" name="_template" value="table">

  <!-- Honeypot anti-spam : DOIT rester vide -->
  <input type="text" name="_honey" style="display:none" tabindex="-1" autocomplete="off">

  <!-- Champs visibles -->
  <input type="text"  name="name"    placeholder="Prénom"  required>
  <input type="tel"   name="phone"   placeholder="Téléphone" required>
  <input type="email" name="email"   placeholder="Email">
  <input type="text"  name="service" placeholder="Type de prestation">
  <textarea name="comment" placeholder="Décrivez votre projet" rows="4" required></textarea>

  <button type="submit">Envoyer ma demande</button>
</form>
```

**⚠️ Mise en service — étapes obligatoires :**

1. Email de réception configuré : `{formsubmit_email}`
   → Modifier si besoin avant livraison du site au client
2. **Première soumission test obligatoire** — formsubmit.co enverra un email de
   confirmation à cette adresse. Cliquer le lien dans cet email pour activer le formulaire.
   Tant que le lien n'est pas cliqué, les soumissions ne sont **pas livrées**.
3. Les valeurs `__SITE_URL__` et `__CLIENT_EMAIL__` seront remplacées par `finalize_site.py`
   à 2 moments : (a) après le 1er deploy avec l'URL Vercel auto pour la démo, (b) après la vente
   avec le custom domain + email du client. Voir POST_DEPLOY.md pour le workflow complet.

---

## 9. COMPOSANTS OBLIGATOIRES

### 9.1 Widget Statut Horaires (Alpine.js)
```html
<div x-data="hoursWidget()" x-init="init()" class="flex items-center gap-2">
  <span class="w-2 h-2 rounded-full" :class="status.open ? 'bg-green-500' : 'bg-red-400'"></span>
  <span class="text-sm font-medium" x-text="status.label"></span>
</div>

<script>
{hours_js}

function hoursWidget() {{
  return {{
    status: {{ open: false, label: 'Chargement…' }},
    init() {{
      this.status = getBusinessStatus();
      setInterval(() => {{ this.status = getBusinessStatus(); }}, 60000);
    }}
  }}
}}
</script>
```

### 9.2 Navigation Mobile Bottom Bar
Sur mobile (< 768px) : remplacer le menu burger par une barre de navigation fixée en bas.
```html
<!-- Desktop nav (hidden on mobile) -->
<nav class="hidden md:flex gap-6">...</nav>

<!-- Mobile bottom bar (hidden on desktop) -->
<nav class="md:hidden fixed bottom-0 left-0 right-0 bg-white border-t z-50 flex">
  <a href="index.html" class="flex-1 flex flex-col items-center py-2 text-xs">
    <span class="material-symbols-outlined text-xl">home</span>
    Accueil
  </a>
  <a href="services.html" class="flex-1 flex flex-col items-center py-2 text-xs">
    <span class="material-symbols-outlined text-xl">build</span>
    Services
  </a>
  <a href="tel:{intl.replace(' ', '') if intl else phone}" class="flex-1 flex flex-col items-center py-2 text-xs text-brand-primary font-bold">
    <span class="material-symbols-outlined text-xl">call</span>
    Appeler
  </a>
  <a href="realisations.html" class="flex-1 flex flex-col items-center py-2 text-xs">
    <span class="material-symbols-outlined text-xl">photo_library</span>
    Travaux
  </a>
  <a href="contact.html" class="flex-1 flex flex-col items-center py-2 text-xs">
    <span class="material-symbols-outlined text-xl">mail</span>
    Devis
  </a>
</nav>
<!-- Padding bottom sur mobile pour compenser la barre fixe -->
<div class="md:hidden h-16"></div>
```

### 9.3 Animations Scroll (Intersection Observer)
```javascript
// Ajouter class .reveal aux éléments à animer à l'apparition
const observer = new IntersectionObserver((entries) => {{
  entries.forEach(entry => {{
    if (entry.isIntersecting) {{
      entry.target.classList.add('animate-fade-up');
      observer.unobserve(entry.target);
    }}
  }});
}}, {{ threshold: 0.1 }});

document.querySelectorAll('.reveal').forEach(el => observer.observe(el));
```

### 9.4 Interactions Emil Design (micro-interactions)
```css
/* Variables d'easing premium */
:root {{
  --ease-out-expo: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-out-quart: cubic-bezier(0.25, 1, 0.5, 1);
  --ease-spring: cubic-bezier(0.23, 1, 0.32, 1);
}}

/* Press effect sur tous les boutons CTA */
.btn-cta {{
  transition: transform 150ms var(--ease-spring), box-shadow 150ms var(--ease-spring);
}}
.btn-cta:hover  {{ transform: scale(1.02); }}
.btn-cta:active {{ transform: scale(0.97); }}

/* Zoom subtle sur hover photos */
.photo-card {{
  overflow: hidden;
}}
.photo-card img {{
  transition: transform 400ms var(--ease-out-expo);
}}
.photo-card:hover img {{ transform: scale(1.04); }}

/* Touch device guard — désactiver hover sur touch */
@media (hover: none) {{
  .btn-cta:hover  {{ transform: none; }}
  .photo-card:hover img {{ transform: none; }}
}}
```

### 9.5 JSON-LD LocalBusiness (SEO)
```html
<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "LocalBusiness",
  "name": "{name}",
  "description": "{editorial if editorial else metier_label + ' à ' + ville}",
  "url": "__SITE_URL__",
  "telephone": "{intl if intl else phone}",
  "address": {{
    "@type": "PostalAddress",
    "streetAddress": "{street_addr}",
    "addressLocality": "{ville}",
    "postalCode": "{code_postal}",
    "addressCountry": "FR"
  }},
  "geo": {{
    "@type": "GeoCoordinates",
    "latitude": {lat if lat else "0"},
    "longitude": {lng if lng else "0"}
  }},
  "aggregateRating": {{
    "@type": "AggregateRating",
    "ratingValue": "{rating}",
    "reviewCount": "{n_rev}",
    "bestRating": "5"
  }},
  "openingHoursSpecification": [],
  "image": "photos/photo_01.jpg",
  "priceRange": "€€",
  "areaServed": "{zone_str}"
}}
</script>
```

### 9.6 Compteur d'avis dynamique (reviews_inline.js)

`reviews_inline.js` (déjà présent dans `site/`) met à jour automatiquement à `DOMContentLoaded`
tous les éléments avec ces classes :
- `.js-review-count`   → reçoit `{n_rev}` (le TOTAL Google, pas le nombre affiché)
- `.js-review-rating`  → reçoit `{rating}`
- `a.js-review-url`    → reçoit l'URL pour déposer un avis Google

**Une seule chose à faire** : inclure `<script src="reviews_inline.js"></script>` dans chaque page
(avant `</head>` ou en haut de `<body>`), puis utiliser les classes :

```html
<!-- Badge hero avec valeurs statiques en fallback (si JS désactivé) -->
<span class="..."><span class="js-review-rating">{rating}</span>/5 · <span class="js-review-count">{n_rev}</span> avis Google</span>

<!-- NumberTicker animé (uses .js-review-count classe + data-target) -->
<span class="js-review-count number-ticker" data-target="{n_rev}">{n_rev}</span>
```

⚠️ Pour le NumberTicker, le `data-target` DOIT valoir `{n_rev}` ({n_rev}), pas la length de reviews.json (qui vaut 12).

---

### 9.7 Bouton "Laisser un avis Google"

Placer ce bouton dans la section avis ET dans le footer. L'URL est dans `reviews_meta.json`.

```html
<a href="#" class="js-review-url inline-flex items-center gap-3 px-6 py-3 bg-white border border-gray-200 rounded-xl text-gray-800 font-semibold text-sm shadow-sm hover:shadow-md transition-all duration-200 group">
  <!-- Google "G" logo SVG -->
  <svg width="18" height="18" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
    <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
    <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
    <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
    <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
    <path fill="none" d="M0 0h48v48H0z"/>
  </svg>
  Laisser un avis
  <span class="material-symbols-outlined text-base group-hover:translate-x-0.5 transition-transform">arrow_forward</span>
</a>
```

> Ce bouton dirige vers la page Google Maps de l'entreprise pour déposer un avis directement.
> L'URL est chargée dynamiquement depuis `reviews_meta.json` via la classe `js-review-url`.

---

## 10. CHECKLIST CONVERSION — VÉRIFIER AVANT PHASE 5

- [ ] `favicon.svg` référencé dans le `<head>` de **chaque** page (`<link rel="icon" href="favicon.svg">`)
- [ ] Téléphone `{phone}` présent dans header (visible sans scroll)
- [ ] Téléphone présent dans hero ET footer
- [ ] Badge `{rating}/5 · {n_rev} avis` visible above the fold
- [ ] CTA "Devis gratuit" présent dans les 3 premières sections
- [ ] Formulaire fonctionnel (FormSubmit.co) avec honeypot `_honey`, `_next`, `_captcha=false`, `_template=table`
- [ ] Widget horaires temps-réel opérationnel
- [ ] Navigation mobile bottom bar (pas hamburger)
- [ ] Photos en `loading="lazy"` sauf hero
- [ ] `alt` text sur chaque `<img>` (contient ville + métier)
- [ ] Embed Google Maps sur contact.html
- [ ] Toutes les pages linkées dans la nav

**Desktop (vérifier à ≥ 1024px) :**
- [ ] Longueur de ligne corps de texte ≤ 72ch sur desktop
- [ ] Grilles de services passent bien en multi-colonnes (`md:grid-cols-2 lg:grid-cols-3`)
- [ ] Barre sticky mobile masquée sur desktop (`md:hidden` confirmé)
- [ ] Titres avec scale responsive — `text-4xl lg:text-6xl` ou équivalent
- [ ] Conteneurs avec `max-w-7xl mx-auto` — rien ne s'étire sur 1440px+
- [ ] Hero lisible et bien cadré à toutes les largeurs (768px / 1280px / 1440px)
- [ ] Mentions légales et politique de confidentialité présentes et liées
- [ ] Meta title sur chaque page (format : `Service à Ville — Nom | Devis gratuit`)
- [ ] Meta description sur chaque page (160 chars max)
- [ ] JSON-LD LocalBusiness sur index.html

---

## 11. SEO — RÈGLES STRICTES

### Meta tags par page
```html
<!-- index.html -->
<title>{metier_label} à {ville} — {name} | Devis gratuit</title>
<meta name="description" content="{name}, {metier_singulier} à {ville}. {n_rev} avis vérifiés. Devis gratuit et sans engagement. Appelez le {phone}.">

<!-- services.html -->
<title>Nos Services {metier_label} à {ville} — {name}</title>

<!-- contact.html -->
<title>Contact & Devis — {name} {metier_singulier} à {ville}</title>
```

### Règles H1
- Une seule balise `<h1>` par page
- L'H1 de index.html DOIT contenir "{ville}"
- Exemple : `{metier_label} à {ville} — {name}`

### Images SEO
- `alt` descriptif : `[description de l'image] — {name} {metier_singulier} {ville}`
- `loading="lazy"` sur toutes sauf la photo hero
- `width` et `height` sur chaque img (évite le layout shift)

### URLs absolues : TOUJOURS utiliser le marqueur `__SITE_URL__`

Si tu ajoutes des meta tags Open Graph, Twitter cards, ou un `<link rel="canonical">`,
**JAMAIS d'URL en dur** (ni `https://h2o-plomberie.fr`, ni `https://votre-site.vercel.app`).
**TOUJOURS** utiliser le marqueur `__SITE_URL__` qui sera remplacé au deploy par `finalize_site.py`.

```html
<!-- Open Graph -->
<meta property="og:url"         content="__SITE_URL__">
<meta property="og:image"       content="__SITE_URL__/photos/photo_01.jpg">
<meta property="og:title"       content="{metier_label} à {ville} — {name}">
<meta property="og:description" content="...">

<!-- Twitter Card -->
<meta name="twitter:url"   content="__SITE_URL__">
<meta name="twitter:image" content="__SITE_URL__/photos/photo_01.jpg">

<!-- Canonical -->
<link rel="canonical" href="__SITE_URL__">
```

Idem pour TOUTE URL absolue qui pointe vers le site (favicon `__SITE_URL__/favicon.svg` dans
manifest.webmanifest, etc.). Pour les liens internes entre pages, utiliser les chemins
relatifs (`href="services.html"`), pas le marqueur.

### Schema JSON-LD FAQPage — OBLIGATOIRE si FAQ présente sur la page

Si tu intègres une **FAQ** sur une page (cf §7 Section 7 + composant FAQAccordion §8 de
COMPONENTS.md), tu DOIS inclure le bloc JSON-LD FAQPage dans le `<head>` de cette page.
C'est ce qui permet à Google d'afficher des rich snippets (ou featured snippets).

**Règle de cohérence** : chaque question/réponse du schema DOIT être **textuellement
présente dans le HTML visible**. Google vérifie. Si tu modifies une question dans le HTML,
modifier aussi le schema (et inversement).

Format complet du bloc dans le composant FAQAccordion (COMPONENTS.md §8). Rappel structure :

```html
<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {{ "@type": "Question", "name": "...", "acceptedAnswer": {{ "@type": "Answer", "text": "..." }} }}
  ]
}}
</script>
```

**Caveat sur les rich snippets** : depuis 2023, Google restreint l'affichage aux sites
"authoritative". Pour un nouveau site, les snippets ne s'afficheront probablement pas
immédiatement, mais le contenu aide Google à comprendre les sujets couverts et s'activera
avec l'ancienneté du domaine.

---

## 12. NEVER DO LIST — INTERDICTIONS ABSOLUES

### Anti-hallucination (priorité CRITIQUE — risque légal pour le client)
- ❌ **Déployer un site avec ≥ 1 [X] dans HALLUCINATIONS_REPORT.md**
- ❌ Inventer une **date de création** ("depuis 1995", "fondée en 2008") non sourcée
- ❌ Inventer un **prénom d'employé** ("Bruno", "Marie commerciale") absent de reviews_all.json
- ❌ Inventer un **prix / tarif** ("à partir de 25 000 €", "forfait dès 89 €") non sourcé
- ❌ Inventer une **garantie** ("décennale", "10 ans satisfait", "30 jours") non mentionnée dans les avis
- ❌ Inventer une **certification** ("RGE", "Qualibat", "Qualifelec") sans preuve scrapée
- ❌ Inventer des **statistiques chiffrées** ("98 % satisfaits", "500 clients", "15 ans expérience") non sourcées
- ❌ Créer une section **"Formules tarifaires"** (Saison/Confort/Premium) sans tarifs publiquement annoncés
- ❌ Créer une **timeline historique** sans dates précises sourcées
- ❌ **Signer un texte au nom du dirigeant** sans citation littérale d'avis

### Design
- ❌ Polices Inter, Outfit, DM Sans, Plus Jakarta Sans, Instrument Sans (utiliser Space Grotesk, Bricolage Grotesque, Geist, Satoshi, ou Cabinet Grotesk)
- ❌ `#000000` pur noir (utiliser `#0F172A` ou `#18181B`)
- ❌ `#FFFFFF` blanc pur — toujours teinter légèrement vers la couleur de marque
- ❌ Couleur palette générée sans extraire les photos (bleu #3B82F6 par défaut, gris neutre sans teinte)
- ❌ Dégradé neon / glow violet-bleu générique
- ❌ Dégradé texte décoratif (`background-clip: text`) — jamais
- ❌ Bordure colorée latérale sur les cards (side-stripe > 1px) — reécrire avec fond teinté ou rien
- ❌ 3 colonnes égales de cards identiques pour les services (icon + titre + 2 lignes)
- ❌ Hero centré avec texte blanc centré sur overlay sombre générique — pattern le plus usé du web artisan
- ❌ Avis clients uniquement en bas de page — les avis sont un argument de vente, pas un appendice
- ❌ CTA unique "en bas de page" — micro-CTA obligatoire dans chaque section
- ❌ 3 sections denses consécutives sans section aérée entre elles
- ❌ Photo stock de bricoleur souriant en combinaison bleue générique
- ❌ Emojis dans le contenu (sauf si la marque les utilise explicitement)
- ❌ Toutes les sections avec le même espacement — rythme variable obligatoire
- ❌ Palette choisie avant d'analyser les photos — la couleur vient des médias, jamais d'un color picker

### Desktop
- ❌ Même taille de titre sur mobile et desktop — toujours un scale responsive (`text-3xl md:text-5xl lg:text-7xl`)
- ❌ Contenu pleine largeur sans `max-w-*` sur desktop — jamais de texte qui s'étire sur 1440px+
- ❌ Barre sticky mobile sans `md:hidden` — elle apparaît sur desktop et recouvre le contenu
- ❌ Colonnes identiques mobile/desktop — les grilles doivent progresser (`grid-cols-1 md:grid-cols-2 lg:grid-cols-3`)
- ❌ Padding identique mobile/desktop — desktop a besoin de plus d'espace vertical (`py-12 lg:py-24`)
- ❌ Hero à hauteur fixe mobile qui ne s'adapte pas sur grand écran — toujours `min-h-[100dvh] lg:min-h-[700px]` ou similaire

### Code
- ❌ `h-screen` (utiliser `min-h-[100dvh]` — iOS Safari bug)
- ❌ `calc()` avec pourcentages pour les layouts (utiliser CSS Grid)
- ❌ `localStorage` ou `sessionStorage`
- ❌ jQuery ou toute librairie CDN non listée au §8
- ❌ Inline styles pour les couleurs (toujours via classe Tailwind ou CSS var)
- ❌ `target="_blank"` sans `rel="noopener noreferrer"`

### Photos & Médias
- ❌ Insérer une photo sans consulter `photos/manifest.json` — source de vérité obligatoire
- ❌ Affecter une photo à un service qu'elle ne montre pas réellement (ex : radiateur dans "recherche de fuite")
- ❌ Utiliser `logo.png` comme fond d'image, en galerie, ou dans les réalisations — logo uniquement
- ❌ Ignorer le dossier `videos/` sans avoir exécuté `ls videos/` — vérification bloquante
- ❌ Inventer une description de photo sans l'avoir analysée visuellement
- ❌ Laisser une section de service sans photo sous prétexte qu'aucune ne "correspond parfaitement" — utiliser `gallery` ou `chantier_general`

### Compteur d'avis
- ❌ Coder le nombre d'avis ou la note en dur dans le HTML sans la classe `js-review-count` / `js-review-rating`
- ❌ Omettre le bouton "Laisser un avis Google" (§9.7) dans la section avis et le footer

### Contenu
- ❌ Texte lorem ipsum ou placeholder
- ❌ Nom d'entreprise fictif ("Acme", "Dupont & Co", "Jean Martin")
- ❌ Faux numéros ronds (`99.9%`, `500+ clients`, `20 ans d'expérience` si non vérifiable)
- ❌ Slogan générique ("Qualité, expertise et professionnalisme")
- ❌ Avis inventés (utiliser uniquement reviews.json)
- ❌ FormSubmit.co email placeholder non remplacé en production

### UX
- ❌ Menu hamburger sur mobile (utiliser bottom bar)
- ❌ Scroll indicator animé ("Scroll to explore ↓")
- ❌ Spinner circulaire de chargement
- ❌ Pop-up ou modal qui s'ouvre au chargement de page
- ❌ Autoplay vidéo avec son
- ❌ Liens morts (`href="#"`) sans action réelle

---

## 13. STRUCTURE DE DEPLOY

Arborescence finale attendue pour Vercel :

**Seul le contenu de `site/` est déployé.** Le workspace (CLAUDE.md, COMPONENTS.md, etc.)
n'est pas poussé sur Vercel.

```
{safe_dirname(name)}/site/        ← DEPLOYABLE — contient TOUT le site final
├── index.html
├── services.html
├── realisations.html
├── a-propos.html
├── contact.html
├── merci.html
├── mentions-legales.html
├── politique-confidentialite.html
├── favicon.svg
├── sitemap.xml + robots.txt
├── reviews.json + reviews_meta.json + reviews_inline.js
├── photos/  (.jpg + .webp)
└── videos/  (.mp4)
```

**Commande deploy** depuis Claude Code :
```
cd site && /deploy
```
ou via Vercel CLI directement : `vercel --cwd=site --prod`.
Vercel détecte automatiquement le site statique — aucune config nécessaire.

---

## 14. COMPOSANTS DISPONIBLES — COMPONENTS.md

> 📎 **La décision composants est traitée en PHASE 1** (§6). Cette section est un simple rappel.

Le fichier `COMPONENTS.md` est présent dans ce dossier. Il contient **10 composants vanilla**
HTML/Alpine.js/CSS pur :

| # | Composant | Usage |
|---|---|---|
| 1 | NumberTicker | Compteur animé scroll (stats, avis) |
| 2 | TextHighlighter | Surlignage éditorial (hero, sections clés) |
| 3 | ImageGallery | Galerie masonry (page réalisations) |
| 4 | BeforeAfterSlider | Comparaison avant/après (si paires photos dispo) |
| 5 | NavigationMenuDesktop | Menu dropdown (si ≥ 5 services) |
| 6 | TestimonialsColumns | Colonnes défilantes avis (si ≥ 8 avis texte) |
| 7 | SimpleReviewCarousel | Carousel avis (alternative si < 8 avis) |
| **8** | **FAQAccordion** | **FAQ extraite des avis + JSON-LD FAQPage (port vanilla du Radix)** |
| **9** | **StaggerReveal** | **Apparition séquencée des éléments (Services + Réalisations max)** |
| **10** | **CursorAwareShadow** | **Ombre qui suit le curseur (Service cards homepage uniquement)** |

Tous avec couleurs paramétriques (`brand-primary`, `text-main`, `surface`) qui héritent
automatiquement de DESIGN.md.

**Règles absolues** :
- ❌ Ne jamais cumuler TestimonialsColumns ET SimpleReviewCarousel sur la même page
- ❌ Ne jamais cumuler ImageGallery ET BeforeAfterSlider sur la même page
- ❌ Ne jamais remplacer `brand-primary` / `text-main` par des valeurs hex hardcodées
- ❌ Ne jamais utiliser StaggerReveal sur plus de 2 sections par page (devient gimmick)
- ❌ Ne jamais utiliser CursorAwareShadow ailleurs que sur les 3-4 Service cards homepage
- ❌ Ne jamais oublier le bloc JSON-LD FAQPage si FAQAccordion utilisé (cohérence schema/HTML)
- ✅ Scripts de chaque composant : avant `</body>`, un seul exemplaire par page

---

_CLAUDE.md généré par company_scraper_bis.py — {datetime.now().strftime("%d/%m/%Y")} — {name}_
"""

    # Écrire le fichier
    out_path = out_dir / "CLAUDE.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"    CLAUDE.md      — {len(doc.splitlines())} lignes")
    return doc


# ─────────────────────────────────────────────────────────────
# FAVICON
# ─────────────────────────────────────────────────────────────

METIER_COLORS = {
    "plumber":            "#1B4F72",
    "electrician":        "#D4AC0D",
    "roofing_contractor": "#784212",
    "painter":            "#2E86AB",
    "general_contractor": "#2C3E50",
    "landscaper":         "#1E8449",
    "locksmith":          "#5D6D7E",
    "hvac_contractor":    "#1A5276",
    "carpenter":          "#6E4C1E",
    "tiler":              "#4A4E69",
    "moving_company":     "#CA6F1E",
    "cleaning_service":   "#148F77",
}


def generate_favicon(name: str, out_dir: Path, types: list = None, design_lookup_dir: Path = None) -> None:
    """
    Génère favicon.svg — initiale de l'entreprise sur fond couleur de marque.
    out_dir : où écrire favicon.svg (typiquement site_dir)
    design_lookup_dir : où chercher DESIGN.md (typiquement le workspace parent)
    """
    # Première lettre alphabétique du nom (en ignorant les formes juridiques)
    name_clean = re.sub(r"^\s*(SARL|SAS|SASU|EURL|EI|SA)\s+", "", name, flags=re.IGNORECASE)
    initial = next((c.upper() for c in name_clean if c.isalpha()), "A")

    # Couleur : DESIGN.md si présent, sinon par métier, sinon fallback
    color = None
    lookup_dir = design_lookup_dir if design_lookup_dir else out_dir
    design_path = lookup_dir / "DESIGN.md"
    if design_path.exists():
        design_text = design_path.read_text(encoding="utf-8")
        m = re.search(r"primary\s*=\s*oklch\([^)]+\)\s*(#[0-9A-Fa-f]{6})", design_text)
        if m:
            color = m.group(1)
    if not color and types:
        color = next((METIER_COLORS[t] for t in types if t in METIER_COLORS), None)
    if not color:
        color = "#1E3A5F"

    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">\n'
        f'  <rect width="32" height="32" rx="6" fill="{color}"/>\n'
        f'  <text x="16" y="23" font-family="system-ui,Arial,sans-serif" font-size="18"'
        f' font-weight="700" fill="white" text-anchor="middle">{initial}</text>\n'
        '</svg>'
    )
    with open(out_dir / "favicon.svg", "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"    favicon.svg    -- '{initial}' sur fond {color}")


# ─────────────────────────────────────────────────────────────
# HELPERS CLAUDE.md — PLACEHOLDERS MENTIONS LÉGALES / RÉSEAUX
# (les sources auto fiables nécessitent API key ou échouent souvent — manuel)
# ─────────────────────────────────────────────────────────────

def _build_legal_block(name: str, address: str) -> str:
    """Bloc mentions légales : placeholder minimal — SIRET + forme juridique uniquement."""
    return "\n".join([
        f"- Raison sociale : **{name}**",
        f"- Adresse : {address}",
        "- **SIRET** : *À compléter manuellement (récupérer sur pappers.fr ou societe.com)*",
        "- **Forme juridique** : *À compléter manuellement*",
        "- Politique : collecte formulaire uniquement, pas de cookies tracking, pas de transfert tiers",
    ])


def _build_social_block(name: str) -> str:
    """Bloc réseaux sociaux : placeholder — demander au client avant livraison."""
    return "\n".join([
        "",
        "**Réseaux sociaux** : demander au client s'il a Facebook / Instagram / TikTok / LinkedIn / YouTube.",
        "Si OUI → ajouter dans le footer des icônes SVG cliquables (Font Awesome 6 brands ou SVG inline).",
        "Si NON → ne pas créer la section.",
    ])


# COMPOSANTS — BIBLIOTHÈQUE VANILLA HTML / ALPINE.JS
# Portés depuis React/Framer Motion → vanilla pur, zéro build step.
# Couleurs paramétriques : brand-primary, text-main, surface, border, text-muted
# → s'adaptent automatiquement à la palette extraite des photos (DESIGN.md §4ter)
# ─────────────────────────────────────────────────────────────

_COMPONENTS_INTRO = r"""# COMPONENTS.md — Bibliothèque de composants vanilla

Composants portés en **HTML/Alpine.js/CSS pur** — stack Tailwind CDN + Alpine.js, zéro React, zéro build.
Couleurs paramétriques (`brand-primary`, `text-main`, `surface`, `border`) → héritent automatiquement de DESIGN.md.

## Règles d'intégration — LIRE AVANT TOUT

1. **Décision par composant** : chaque section indique `Quand l'utiliser` et `Décision` — toujours lire avant d'intégrer.
2. **Pas de force** : si un composant ne s'intègre pas naturellement dans le layout (§4ter), ne pas l'utiliser.
3. **Couleurs** : ne jamais remplacer `brand-primary` / `text-main` etc. par des valeurs hex hardcodées.
4. **Placeholders** : tout texte en `NOM_ENTREPRISE`, `PHOTO_BEFORE`, `SERVICE 1` → remplacer par les vraies données §2.
5. **Scripts** : chaque composant inclut son script → placer avant `</body>`. Un seul exemplaire par page même si plusieurs instances.

---

"""

_COMPONENT_NUMBER_TICKER = r"""## 1. NumberTicker — Compteur animé au scroll

**Quand l'utiliser** : Afficher des chiffres clés animés dès qu'ils entrent dans le viewport.
Idéal pour : nombre d'avis, années d'expérience, nombre de chantiers réalisés.
**Position recommandée** : Trust bar, section stats hero, badges de confiance.
**Compatible** : fonctionne automatiquement avec les classes `js-review-count` et `js-review-rating`.

**Décision** : Utiliser si le site a une trust bar ou une section stats avec des chiffres numériques.
Ne pas créer une section stats juste pour ce composant — il doit s'insérer dans un bloc existant.

```html
<!-- Ajouter class="number-ticker" + data-target="VALEUR" sur tout élément numérique -->
<!-- Exemples : -->
<span class="number-ticker js-review-count font-display font-bold text-brand-primary tabular-nums"
      data-target="208" data-duration="1500">208</span> avis vérifiés

<span class="number-ticker js-review-rating font-display font-bold tabular-nums"
      data-target="4.9" data-decimals="1" data-duration="1200">4.9</span>/5

<!-- Script — UNE SEULE FOIS par page, avant </body> -->
<script>
(function(){
  if(window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  function easeOutQuart(t){ return 1-Math.pow(1-t,4); }
  function tick(el){
    const target=parseFloat(el.dataset.target||el.textContent.replace(/\s/g,''));
    const dur=parseInt(el.dataset.duration||'1500');
    const dec=parseInt(el.dataset.decimals||'0');
    const from=parseFloat(el.dataset.start||'0');
    let t0=null;
    function upd(ts){
      if(!t0)t0=ts;
      const p=Math.min((ts-t0)/dur,1);
      const v=from+(target-from)*easeOutQuart(p);
      el.textContent=dec>0?v.toFixed(dec):Math.round(v).toLocaleString('fr-FR');
      if(p<1)requestAnimationFrame(upd);
    }
    requestAnimationFrame(upd);
  }
  const obs=new IntersectionObserver(entries=>{
    entries.forEach(e=>{ if(e.isIntersecting){ tick(e.target); obs.unobserve(e.target); } });
  },{threshold:0.3});
  document.querySelectorAll('.number-ticker').forEach(el=>obs.observe(el));
})();
</script>
```

---

"""

_COMPONENT_TEXT_HIGHLIGHTER = r"""## 2. TextHighlighter — Surlignage éditorial ANIMÉ au scroll

**Quand l'utiliser** : Mettre en valeur 1 à 2 mots-clés forts dans une headline ou un sous-titre.
Idéal pour : hero headline, promesses services, section à-propos.
**Position recommandée** : H1 hero, H2 sections clés. Max 2 highlights par page.

**Décision** : Utiliser si la direction typographique est Éditoriale (§4ter DESIGN.md).
Les 2 modes sont **animés au scroll** (IntersectionObserver) :
- `--underline` → trait qui se dessine de gauche à droite (250-450ms)
- `--bg`        → fond qui se remplit de gauche à droite (550ms)
Ne pas utiliser si plus de 2 mots sont déjà en gras/couleur dans le même bloc — trop de relief annule l'effet.

### Choix de couleur — adapter à DESIGN.md

Par défaut le composant utilise `var(--color-accent)` (couleur d'accent du DESIGN.md, ex: cuivre laiton).
Pour utiliser une autre couleur de marque sur un highlight précis :

```html
<!-- Defaut = accent du site -->
<span class="txt-hl txt-hl--underline">votre salle de bain</span>

<!-- Forcer primary (couleur dominante de marque) -->
<span class="txt-hl txt-hl--bg" style="--hl-color: var(--color-primary);">résultat garanti</span>

<!-- Forcer secondary -->
<span class="txt-hl txt-hl--underline" style="--hl-color: var(--color-secondary);">réactivité</span>
```

❌ Ne jamais mettre une valeur hex en dur (`--hl-color: #C9994E;`). Toujours via les `var(--color-*)` du DESIGN.md.

### Code complet

```html
<!-- Usage : ajouter class="txt-hl txt-hl--underline" ou class="txt-hl txt-hl--bg" -->
<h1>
  Plombier de confiance à <span class="txt-hl txt-hl--underline">Reims</span>
  pour un <span class="txt-hl txt-hl--bg">résultat garanti</span>.
</h1>

<!-- Styles : a inclure une seule fois dans le CSS global ou <head> -->
<style>
.txt-hl {
  position: relative;
  display: inline-block;
  --hl-color: var(--color-accent, #C9994E);
}

/* --- Mode 1 : SOULIGNEMENT anime (trait qui se dessine) --- */
.txt-hl--underline {
  background-image: linear-gradient(var(--hl-color), var(--hl-color));
  background-repeat: no-repeat;
  background-size: 0% 3px;
  background-position: 0 100%;
  padding-bottom: 4px;
  transition: background-size 0.45s cubic-bezier(0.65, 0, 0.35, 1);
}
.txt-hl--underline.hl-on { background-size: 100% 3px; }

/* --- Mode 2 : SURLIGNAGE FOND anime (fond qui se remplit) --- */
.txt-hl--bg {
  background-image: linear-gradient(120deg, var(--hl-color) 0%, var(--hl-color) 100%);
  background-repeat: no-repeat;
  background-size: 0% 38%;
  background-position: 0 88%;
  padding: 0 4px;
  transition: background-size 0.55s cubic-bezier(0.25, 1, 0.5, 1);
}
.txt-hl--bg.hl-on { background-size: 100% 38%; }

/* Accessibilite : afficher direct si l'utilisateur prefere moins de mouvement */
@media (prefers-reduced-motion: reduce) {
  .txt-hl--underline, .txt-hl--bg { transition: none; }
}
</style>

<script>
(function() {
  const hls = document.querySelectorAll('.txt-hl--underline, .txt-hl--bg');
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    hls.forEach(el => el.classList.add('hl-on'));
    return;
  }
  const obs = new IntersectionObserver(entries => {
    entries.forEach(e => {
      if (e.isIntersecting) {
        // petit delai pour que le trait/fond se dessine bien apres l'apparition
        setTimeout(() => e.target.classList.add('hl-on'), 120);
        obs.unobserve(e.target);
      }
    });
  }, { threshold: 0.5 });
  hls.forEach(el => obs.observe(el));
  // Fallback : si scroll Playwright/jamais detecte, force apres 1.5s
  setTimeout(() => hls.forEach(el => el.classList.add('hl-on')), 1500);
})();
</script>
```

---

"""

_COMPONENT_IMAGE_GALLERY = r"""## 3. ImageGallery — Galerie masonry avec fade-in au scroll

**Quand l'utiliser** : Page réalisations — systématiquement. Page index si 6+ photos de chantiers disponibles.
**Prérequis** : `photos/manifest.json` existant — utiliser uniquement photos `quality: "high"` ou `"medium"`.

**Décision** : Intégrer sur `realisations.html` toujours.
Sur `index.html` : utiliser la grille asymétrique (4-6 photos max) plutôt que les colonnes complètes.
Ne pas mélanger ImageGallery et BeforeAfterSlider sur la même page — choisir l'un ou l'autre.

```html
<!-- Galerie masonry 3 colonnes — realisations.html -->
<!-- Ordre des photos : "high" quality en premier, affectation "gallery" puis "hero" -->
<div class="img-gallery columns-1 sm:columns-2 lg:columns-3 gap-4 px-4 max-w-7xl mx-auto"
     style="column-gap: 1rem;">

  <!-- Répéter ce bloc pour chaque photo issue de manifest.json -->
  <figure class="gallery-item break-inside-avoid mb-4 overflow-hidden rounded-2xl bg-surface group">
    <img
      src="photos/photo_01.jpg"
      alt="Réalisation NOM_ENTREPRISE — DESCRIPTION_MANIFEST — VILLE"
      class="gallery-img w-full object-cover transition-all duration-700 opacity-0 group-hover:scale-[1.03]"
      loading="lazy" width="800" height="600">
    <!-- Caption si manifest.json contient une description précise -->
    <!-- <figcaption class="px-4 py-2.5 text-xs text-text-muted">Description courte</figcaption> -->
  </figure>
  <!-- /photo -->

</div>

<style>
.gallery-img.gl-in { opacity: 1; }
@media (hover: hover) {
  .gallery-item { transition: transform 0.35s cubic-bezier(0.16,1,0.3,1); }
  .gallery-item:hover { transform: scale(1.015); }
}
</style>
<script>
(function(){
  const obs=new IntersectionObserver(entries=>{
    entries.forEach(e=>{
      if(!e.isIntersecting) return;
      const img=e.target;
      if(img.complete) img.classList.add('gl-in');
      else img.addEventListener('load',()=>img.classList.add('gl-in'));
      obs.unobserve(img);
    });
  },{threshold:0.05});
  document.querySelectorAll('.gallery-img').forEach(img=>{
    img.addEventListener('error',()=>img.closest('.gallery-item').style.display='none');
    obs.observe(img);
  });
})();
</script>
```

---

"""

_COMPONENT_BEFORE_AFTER = r"""## 4. BeforeAfterSlider — Comparaison avant/après travaux

**Quand l'utiliser** : Si `photos/manifest.json` contient 2+ photos permettant une comparaison pertinente
(même pièce avant/après rénovation, état dégradé vs résultat propre).
**Position recommandée** : Section Réalisations, section Services (illustrer une prestation).

**Décision** : Vérifier manifest.json — chercher des paires logiques (ex: salle de bain délabrée + rénovée).
Si aucune paire disponible → ne pas utiliser. C'est le composant le plus impactant pour la conversion
artisan — le prioriser si les photos le permettent. Ne pas cumuler avec ImageGallery sur la même page.

```html
<!-- BeforeAfterSlider : remplacer PHOTO_BEFORE et PHOTO_AFTER par les vraies photos -->
<!-- Le handle "Avant"/"Après" utilise bg-brand-primary automatiquement -->
<div class="relative w-full max-w-3xl mx-auto rounded-2xl overflow-hidden shadow-2xl select-none ba-container"
     style="touch-action: pan-y;">

  <!-- Labels positionnés -->
  <span class="absolute top-4 left-4 z-20 bg-black/60 text-white text-xs font-semibold px-3 py-1.5 rounded-full uppercase tracking-widest pointer-events-none">Avant</span>
  <span class="absolute top-4 right-4 z-20 bg-brand-primary text-white text-xs font-semibold px-3 py-1.5 rounded-full uppercase tracking-widest pointer-events-none">Après</span>

  <!-- Image Avant (fond) -->
  <img src="photos/PHOTO_BEFORE.jpg"
       alt="Avant travaux — NOM_ENTREPRISE VILLE"
       class="block w-full h-auto object-cover pointer-events-none" draggable="false">

  <!-- Image Après (clip dynamique) -->
  <div class="ba-after absolute inset-0 overflow-hidden" style="clip-path: inset(0 50% 0 0);">
    <img src="photos/PHOTO_AFTER.jpg"
         alt="Après travaux — NOM_ENTREPRISE VILLE"
         class="w-full h-full object-cover pointer-events-none" draggable="false">
  </div>

  <!-- Handle de glissement -->
  <div class="ba-handle absolute top-0 bottom-0 flex items-center justify-center z-10 cursor-ew-resize"
       style="left:50%; transform:translateX(-50%); width:3rem;"
       tabindex="0" role="slider" aria-label="Comparaison avant/après — glisser">
    <div class="w-px h-full bg-white/70 absolute left-1/2 -translate-x-1/2 pointer-events-none"></div>
    <div class="relative w-10 h-10 bg-white rounded-full shadow-xl flex items-center justify-center ba-btn transition-transform duration-150">
      <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#374151" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        <polyline points="15 18 9 12 15 6"/><polyline points="9 18 15 12 9 6"/>
      </svg>
    </div>
  </div>
</div>

<script>
(function(){
  document.querySelectorAll('.ba-container').forEach(c=>{
    const handle=c.querySelector('.ba-handle');
    const after=c.querySelector('.ba-after');
    const btn=c.querySelector('.ba-btn');
    let active=false;
    function move(x){
      const r=c.getBoundingClientRect();
      const pct=Math.max(5,Math.min(95,((x-r.left)/r.width)*100));
      handle.style.left=pct+'%';
      after.style.clipPath='inset(0 '+(100-pct)+'% 0 0)';
    }
    handle.addEventListener('mousedown',e=>{active=true;btn.style.transform='scale(1.1)';e.preventDefault();});
    handle.addEventListener('touchstart',()=>{active=true;btn.style.transform='scale(1.1)';},{passive:true});
    c.addEventListener('mousemove',e=>{if(active)move(e.clientX);});
    c.addEventListener('touchmove',e=>{if(active)move(e.touches[0].clientX);},{passive:true});
    window.addEventListener('mouseup',()=>{active=false;btn.style.transform='';});
    window.addEventListener('touchend',()=>{active=false;btn.style.transform='';});
    handle.addEventListener('keydown',e=>{
      const r=c.getBoundingClientRect();
      const cur=parseFloat(handle.style.left)||50;
      const step=e.shiftKey?10:3;
      if(e.key==='ArrowLeft') move(r.left+(cur-step)/100*r.width);
      if(e.key==='ArrowRight') move(r.left+(cur+step)/100*r.width);
    });
  });
})();
</script>
```

---

"""

_COMPONENT_NAV_DESKTOP = r"""## 5. NavigationMenuDesktop — Menu desktop avec dropdown Alpine.js

**Quand l'utiliser** : Si l'artisan a 5+ services distincts méritant des sous-catégories dans la nav.
Pour ≤ 4 services → navigation simple (liens directs) sans dropdown.
**Position** : Header desktop uniquement (`hidden md:flex` — le bottom bar §9.2 gère le mobile).

**Décision** : Par défaut utiliser la nav simple. Upgrader vers ce composant uniquement si les services
sont suffisamment nombreux et distincts pour justifier un menu à deux niveaux.
Ne pas créer un dropdown pour avoir ce composant — la nav simple est souvent plus efficace.

```html
<!-- Navigation desktop avec dropdown conditionnel -->
<!-- Remplacer SERVICE 1/2/3 par les vrais services extraits des types Google + avis -->
<!-- Icônes Material Symbols : choisir l'icône la plus précise par service -->
<nav class="hidden md:flex items-center gap-0.5" x-data="{ open: null }">

  <a href="index.html"
     class="px-4 py-2 text-sm font-medium rounded-lg text-text-main hover:bg-surface hover:text-brand-primary transition-colors duration-150">
    Accueil
  </a>

  <!-- Dropdown Services — utiliser seulement si ≥ 5 services distincts -->
  <div class="relative" @mouseenter="open='services'" @mouseleave="open=null">
    <button class="flex items-center gap-1.5 px-4 py-2 text-sm font-medium rounded-lg text-text-main hover:bg-surface hover:text-brand-primary transition-colors duration-150">
      Services
      <svg class="w-3.5 h-3.5 opacity-50 transition-transform duration-200"
           :class="{'rotate-180': open==='services'}"
           viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <polyline points="6 9 12 15 18 9"/>
      </svg>
    </button>
    <div x-show="open==='services'"
         x-transition:enter="transition ease-out duration-150"
         x-transition:enter-start="opacity-0 -translate-y-1"
         x-transition:enter-end="opacity-100 translate-y-0"
         x-transition:leave="transition ease-in duration-100"
         x-transition:leave-start="opacity-100 translate-y-0"
         x-transition:leave-end="opacity-0 -translate-y-1"
         class="absolute top-full left-0 mt-2 w-60 bg-white rounded-xl border border-border shadow-lg shadow-black/5 p-1.5 z-50">
      <!-- Un <a> par service principal — adapter l'icône et le libellé -->
      <a href="services.html#service-1"
         class="flex items-center gap-3 px-3 py-2.5 text-sm rounded-lg text-text-main hover:bg-surface hover:text-brand-primary transition-colors duration-150 group">
        <span class="material-symbols-outlined text-xl text-brand-secondary group-hover:text-brand-primary transition-colors">plumbing</span>
        SERVICE 1
      </a>
      <a href="services.html#service-2"
         class="flex items-center gap-3 px-3 py-2.5 text-sm rounded-lg text-text-main hover:bg-surface hover:text-brand-primary transition-colors duration-150 group">
        <span class="material-symbols-outlined text-xl text-brand-secondary group-hover:text-brand-primary transition-colors">local_fire_department</span>
        SERVICE 2
      </a>
      <a href="services.html#service-3"
         class="flex items-center gap-3 px-3 py-2.5 text-sm rounded-lg text-text-main hover:bg-surface hover:text-brand-primary transition-colors duration-150 group">
        <span class="material-symbols-outlined text-xl text-brand-secondary group-hover:text-brand-primary transition-colors">build</span>
        SERVICE 3
      </a>
      <div class="h-px bg-border mx-2 my-1.5"></div>
      <a href="services.html"
         class="flex items-center gap-2 px-3 py-2.5 text-sm font-medium rounded-lg text-brand-primary hover:bg-surface transition-colors duration-150">
        Tous nos services
        <span class="material-symbols-outlined text-base">arrow_forward</span>
      </a>
    </div>
  </div>

  <a href="realisations.html"
     class="px-4 py-2 text-sm font-medium rounded-lg text-text-main hover:bg-surface hover:text-brand-primary transition-colors duration-150">
    Réalisations
  </a>
  <a href="a-propos.html"
     class="px-4 py-2 text-sm font-medium rounded-lg text-text-main hover:bg-surface hover:text-brand-primary transition-colors duration-150">
    À propos
  </a>
  <a href="contact.html"
     class="px-4 py-2 text-sm font-medium rounded-lg text-text-main hover:bg-surface hover:text-brand-primary transition-colors duration-150">
    Contact
  </a>

</nav>
```

---

"""

_COMPONENT_TESTIMONIALS = r"""## 6. TestimonialsColumns — Colonnes de témoignages en défilement infini

**Quand l'utiliser** : Si `reviews.json` contient **8+ avis avec du texte**. Remplace le carousel §9.5 —
ne pas utiliser les deux sur la même page.
**Position recommandée** : Section avis dédiée (après Services, avant Formulaire) sur index.html.

**Décision** : Utiliser si ≥ 8 avis texte disponibles. Si < 8 → carousel §9.5 à la place.
Les 3 colonnes défilent à des vitesses différentes (22s/28s/19s) pour un effet organique.
La colonne centrale défile en sens inverse. Pause au hover sur chaque colonne.
Charge `reviews.json` automatiquement — aucune donnée à copier-coller.

```html
<!-- TestimonialsColumns — charge reviews.json automatiquement -->
<!-- Couleurs et typographie héritées de DESIGN.md via les classes brand-* et text-* -->
<section class="overflow-hidden py-20 lg:py-32 bg-surface">
  <div class="max-w-7xl mx-auto px-4 sm:px-6">

    <!-- En-tête -->
    <div class="text-center mb-16 reveal">
      <p class="text-sm font-semibold uppercase tracking-widest text-brand-secondary mb-3">Avis clients</p>
      <h2 class="text-3xl lg:text-5xl font-display font-bold text-text-main mb-4">
        Ce que disent nos clients
      </h2>
      <p class="text-text-muted">
        <span class="js-review-rating font-semibold text-text-main">5</span>/5 &middot;
        <span class="js-review-count font-semibold text-text-main">208</span> avis vérifiés Google
      </p>
    </div>

    <!-- Colonnes défilantes -->
    <div x-data="testimonialsColumns()" x-init="init()"
         class="flex gap-5 items-start overflow-hidden"
         style="height:640px; mask-image:linear-gradient(to bottom,transparent 0%,black 8%,black 92%,transparent 100%); -webkit-mask-image:linear-gradient(to bottom,transparent 0%,black 8%,black 92%,transparent 100%);">

      <!-- Colonne 1 — défilement montant -->
      <div class="flex-1 overflow-hidden h-full" x-show="cols[0]&&cols[0].length>0">
        <div class="tsc-col" :style="`animation-duration:${speeds[0]}s`"
             @mouseenter="$el.style.animationPlayState='paused'"
             @mouseleave="$el.style.animationPlayState='running'">
          <template x-for="(r,i) in [...(cols[0]||[]),...(cols[0]||[])]" :key="'a'+i">
            <div class="tsc-card mb-5 p-6 bg-white rounded-2xl border border-border shadow-sm">
              <div class="flex gap-0.5 mb-3">
                <template x-for="s in r.rating" :key="s">
                  <svg class="w-4 h-4 fill-yellow-400" viewBox="0 0 20 20"><path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"/></svg>
                </template>
              </div>
              <p class="text-sm text-text-muted leading-relaxed mb-4 line-clamp-5" x-text="r.text"></p>
              <div class="flex items-center gap-3">
                <img :src="r.image" :alt="r.name" loading="lazy"
                     class="w-9 h-9 rounded-full object-cover bg-surface flex-shrink-0"
                     onerror="this.src='https://ui-avatars.com/api/?name='+encodeURIComponent(this.alt)+'&background=e2e8f0&color=64748b&size=36'">
                <div class="min-w-0">
                  <p class="text-sm font-semibold text-text-main truncate" x-text="r.name"></p>
                  <p class="text-xs text-text-muted truncate" x-text="r.role"></p>
                </div>
              </div>
            </div>
          </template>
        </div>
      </div>

      <!-- Colonne 2 — défilement descendant (inverse) -->
      <div class="flex-1 overflow-hidden h-full hidden md:block" x-show="cols[1]&&cols[1].length>0">
        <div class="tsc-col tsc-col--rev" :style="`animation-duration:${speeds[1]}s`"
             @mouseenter="$el.style.animationPlayState='paused'"
             @mouseleave="$el.style.animationPlayState='running'">
          <template x-for="(r,i) in [...(cols[1]||[]),...(cols[1]||[])]" :key="'b'+i">
            <div class="tsc-card mb-5 p-6 bg-white rounded-2xl border border-border shadow-sm">
              <div class="flex gap-0.5 mb-3">
                <template x-for="s in r.rating" :key="s">
                  <svg class="w-4 h-4 fill-yellow-400" viewBox="0 0 20 20"><path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"/></svg>
                </template>
              </div>
              <p class="text-sm text-text-muted leading-relaxed mb-4 line-clamp-5" x-text="r.text"></p>
              <div class="flex items-center gap-3">
                <img :src="r.image" :alt="r.name" loading="lazy"
                     class="w-9 h-9 rounded-full object-cover bg-surface flex-shrink-0"
                     onerror="this.src='https://ui-avatars.com/api/?name='+encodeURIComponent(this.alt)+'&background=e2e8f0&color=64748b&size=36'">
                <div class="min-w-0">
                  <p class="text-sm font-semibold text-text-main truncate" x-text="r.name"></p>
                  <p class="text-xs text-text-muted truncate" x-text="r.role"></p>
                </div>
              </div>
            </div>
          </template>
        </div>
      </div>

      <!-- Colonne 3 — desktop large, défilement montant -->
      <div class="flex-1 overflow-hidden h-full hidden lg:block" x-show="cols[2]&&cols[2].length>0">
        <div class="tsc-col" :style="`animation-duration:${speeds[2]}s`"
             @mouseenter="$el.style.animationPlayState='paused'"
             @mouseleave="$el.style.animationPlayState='running'">
          <template x-for="(r,i) in [...(cols[2]||[]),...(cols[2]||[])]" :key="'c'+i">
            <div class="tsc-card mb-5 p-6 bg-white rounded-2xl border border-border shadow-sm">
              <div class="flex gap-0.5 mb-3">
                <template x-for="s in r.rating" :key="s">
                  <svg class="w-4 h-4 fill-yellow-400" viewBox="0 0 20 20"><path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"/></svg>
                </template>
              </div>
              <p class="text-sm text-text-muted leading-relaxed mb-4 line-clamp-5" x-text="r.text"></p>
              <div class="flex items-center gap-3">
                <img :src="r.image" :alt="r.name" loading="lazy"
                     class="w-9 h-9 rounded-full object-cover bg-surface flex-shrink-0"
                     onerror="this.src='https://ui-avatars.com/api/?name='+encodeURIComponent(this.alt)+'&background=e2e8f0&color=64748b&size=36'">
                <div class="min-w-0">
                  <p class="text-sm font-semibold text-text-main truncate" x-text="r.name"></p>
                  <p class="text-xs text-text-muted truncate" x-text="r.role"></p>
                </div>
              </div>
            </div>
          </template>
        </div>
      </div>

    </div>

    <!-- CTA avis Google -->
    <div class="flex justify-center mt-10">
      <a href="#" class="js-review-url inline-flex items-center gap-3 px-6 py-3 bg-white border border-border rounded-xl text-text-main font-semibold text-sm shadow-sm hover:shadow-md transition-all duration-200 group">
        <svg width="18" height="18" viewBox="0 0 48 48" xmlns="http://www.w3.org/2000/svg">
          <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>
          <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>
          <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>
          <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>
        </svg>
        Laisser un avis
        <span class="material-symbols-outlined text-base group-hover:translate-x-0.5 transition-transform">arrow_forward</span>
      </a>
    </div>

  </div>
</section>

<style>
@keyframes tsc-up   { from{transform:translateY(0)}   to{transform:translateY(-50%)} }
@keyframes tsc-down { from{transform:translateY(-50%)} to{transform:translateY(0)}   }
.tsc-col     { animation: tsc-up   linear infinite; }
.tsc-col--rev { animation: tsc-down linear infinite; }
@media (prefers-reduced-motion: reduce) { .tsc-col, .tsc-col--rev { animation: none; } }
</style>
<script>
function testimonialsColumns() {
  return {
    cols: [[],[],[]],
    speeds: [22, 28, 19],
    init() {
      // Source : window.SITE_REVIEWS (charge par reviews_inline.js — synchrone, file://-safe)
      const all = window.SITE_REVIEWS || [];
      const q = all.filter(x => x.text && x.text.trim().length > 30);
      q.forEach((rev, i) => this.cols[i % 3].push(rev));
      if (q.length === 0) console.warn('TestimonialsColumns: window.SITE_REVIEWS vide. Verifier que reviews_inline.js est inclus AVANT ce script.');
    }
  };
}
</script>
```
"""


_COMPONENT_SIMPLE_CAROUSEL = r"""## 7. SimpleReviewCarousel — Carousel avis Alpine.js

**Quand l'utiliser** : Alternative à TestimonialsColumns (§6) quand `reviews.json` contient < 8 avis texte.
Plus compact, autoplay, scroll horizontal, fonctionne avec 3+ avis.
**Position recommandée** : Section dédiée avis sur index.html.

**Décision** : Si TestimonialsColumns ne s'applique pas (< 8 avis) ET qu'on veut afficher les avis
de façon dynamique → utiliser ce carousel. Sinon, ne pas l'utiliser.
Ne JAMAIS cumuler les deux composants d'avis sur la même page.

```html
<div x-data="simpleReviewCarousel()" x-init="init()" class="overflow-hidden">
  <div class="flex gap-4 transition-transform duration-500 ease-out"
       :style="`transform: translateX(-${current * (100/visible)}%)`">
    <template x-for="r in reviews" :key="r.name">
      <div class="min-w-[300px] md:min-w-[340px] bg-white rounded-2xl p-6 shadow-sm border border-border">
        <div class="flex gap-0.5 mb-3">
          <template x-for="i in r.rating" :key="i">
            <svg class="w-4 h-4 fill-yellow-400" viewBox="0 0 20 20"><path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z"/></svg>
          </template>
        </div>
        <p class="text-sm text-text-muted leading-relaxed mb-4 line-clamp-5" x-text="r.text"></p>
        <div class="flex items-center gap-3">
          <img :src="r.image" :alt="r.name"
               class="w-10 h-10 rounded-full object-cover flex-shrink-0"
               onerror="this.src='https://ui-avatars.com/api/?name='+encodeURIComponent(this.alt)+'&background=e2e8f0&color=64748b&size=40'">
          <div class="min-w-0">
            <p class="font-semibold text-sm text-text-main truncate" x-text="r.name"></p>
            <p class="text-xs text-text-muted truncate" x-text="r.role"></p>
          </div>
        </div>
      </div>
    </template>
  </div>

  <!-- Dots de navigation -->
  <div class="flex justify-center gap-2 mt-6">
    <template x-for="(r, i) in reviews" :key="i">
      <button @click="current = i" :class="current === i ? 'bg-brand-primary w-6' : 'bg-border w-2'"
              class="h-2 rounded-full transition-all duration-300" :aria-label="`Avis ${i+1}`"></button>
    </template>
  </div>
</div>

<script>
function simpleReviewCarousel() {
  return {
    reviews: [],
    current: 0,
    visible: 1,
    init() {
      // Source : window.SITE_REVIEWS (charge par reviews_inline.js — synchrone, file://-safe)
      this.reviews = window.SITE_REVIEWS || [];
      this.visible = window.innerWidth >= 1024 ? 3 : window.innerWidth >= 640 ? 2 : 1;
      const max = Math.max(1, this.reviews.length - this.visible + 1);
      if (this.reviews.length > 1) setInterval(() => { this.current = (this.current + 1) % max; }, 5000);
      if (this.reviews.length === 0) console.warn('SimpleReviewCarousel: window.SITE_REVIEWS vide. Verifier que reviews_inline.js est inclus AVANT ce script.');
    }
  };
}
</script>
```
"""


_COMPONENT_FAQ_ACCORDION = r"""## 8. FAQAccordion — Questions/réponses sourcées des avis (port vanilla du Radix Accordion)

**Quand l'utiliser** : index.html (5 questions générales, placée entre Avis et Zone d'intervention) +
chaque page service (5-8 questions spécifiques au service).

**Décision** : TOUJOURS si reviews_all.json contient ≥ 30 avis (matière suffisante pour extraire
de vraies questions du vécu client). Si < 30 avis : skip (risque d'inventer des questions).

### Méthode d'extraction des questions

Voir CLAUDE.md §7 Section FAQ pour la méthode complète. En résumé :
1. Lire reviews_all.json en cherchant peurs implicites + éloges récurrents
2. Regrouper par catégorie d'objection : Réactivité / Méthode / Tarif / Zone d'intervention
3. 5-8 questions max, chaque réponse Mode 1 sourcé OU Mode 2 générique + micro-CTA naturel en fin

❌ Question/réponse générique : "Travaillez-vous bien ? Oui nous travaillons bien."
✅ Question/réponse sourcée : "À quelle vitesse Karim peut-il intervenir ? Pour les urgences —
   fuite active, dégât des eaux — Karim intervient généralement le jour même sur Reims.
   Appelez le 03 26 88 12 34 pour vérifier sa disponibilité."

### Code HTML (vanilla — fidèle au Radix Accordion fourni, zéro JS pour le mécanisme)

```html
<section class="faq-section py-16 lg:py-24 bg-surface">
  <div class="max-w-3xl mx-auto px-4 sm:px-6">

    <div class="text-center mb-12">
      <p class="text-sm font-semibold uppercase tracking-widest text-brand-secondary mb-3">
        Questions frequentes
      </p>
      <h2 class="text-3xl lg:text-5xl font-display font-bold text-text-main">
        Tout ce que vous voulez savoir
      </h2>
    </div>

    <div class="accordion">

      <!-- Répéter ce bloc <details> pour chaque question. name="faq-index" force le mode single-open. -->
      <details class="accordion-item border-b border-border" name="faq-index">
        <summary class="accordion-trigger flex items-center justify-between py-4 text-left font-semibold cursor-pointer list-none hover:underline">
          <span class="pr-4">À quelle vitesse pouvez-vous intervenir ?</span>
          <svg class="accordion-chevron shrink-0 opacity-60 transition-transform duration-200"
               width="16" height="16" viewBox="0 0 16 16" fill="none"
               stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="4 6 8 10 12 6"/>
          </svg>
        </summary>
        <div class="accordion-content">
          <div class="accordion-content-inner pb-4 pt-0 text-text-muted leading-relaxed">
            REPONSE ICI avec micro-CTA naturel en fin :
            <a href="tel:+33XXXXXXXXX" class="text-brand-primary font-semibold underline-offset-2 hover:underline">
              XX XX XX XX XX
            </a>.
          </div>
        </div>
      </details>

      <!-- ... 4 à 7 autres <details>... -->

    </div>
  </div>
</section>

<style>
/* Cacher la fleche par defaut du <details> sur tous navigateurs */
.accordion-item > summary::-webkit-details-marker { display: none; }
.accordion-item > summary::marker { content: ""; }

/* Chevron rotation 180deg quand ouvert (equivalent Radix data-state=open) */
.accordion-item[open] .accordion-chevron {
  transform: rotate(180deg);
}

/* Animation slide-down via grid-template-rows (CSS moderne, equivalent Radix accordion-down) */
.accordion-content {
  display: grid;
  grid-template-rows: 0fr;
  transition: grid-template-rows 250ms cubic-bezier(0.16, 1, 0.3, 1);
}
.accordion-item[open] .accordion-content {
  grid-template-rows: 1fr;
}
.accordion-content-inner {
  overflow: hidden;
}

@media (prefers-reduced-motion: reduce) {
  .accordion-chevron, .accordion-content { transition: none; }
}
</style>
```

### Bloc JSON-LD FAQPage (OBLIGATOIRE à inclure dans `<head>` de la page contenant la FAQ)

```html
<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "FAQPage",
  "mainEntity": [
    {{
      "@type": "Question",
      "name": "QUESTION 1 EXACTEMENT IDENTIQUE AU HTML",
      "acceptedAnswer": {{
        "@type": "Answer",
        "text": "REPONSE 1 en texte brut (pas de HTML, pas de markdown, pas de balise <a>)"
      }}
    }},
    {{
      "@type": "Question",
      "name": "QUESTION 2",
      "acceptedAnswer": {{
        "@type": "Answer",
        "text": "REPONSE 2"
      }}
    }}
  ]
}}
</script>
```

⚠️ **Cohérence schema ↔ HTML obligatoire** : Google vérifie que les questions du schema sont
textuellement présentes dans le HTML visible. Si tu modifies une question, modifier les DEUX endroits.

⚠️ **Sur les rich snippets** : depuis 2023, Google restreint l'affichage des FAQ rich snippets
aux sites "authoritative". Pour un nouveau site, ils ne s'afficheront probablement pas
immédiatement. Mais (a) ils s'activeront avec l'ancienneté du domaine, (b) le contenu FAQ aide
Google à comprendre les sujets couverts, (c) les featured snippets utilisent aussi ce contenu.
C'est du travail qui porte toujours.

### Différences vs le Radix original

| Aspect | Radix (React) | Vanilla port |
|---|---|---|
| State management | `data-state="open/closed"` | `<details open>` natif HTML5 |
| Exclusivité single-open | prop `type="single"` | attribute HTML5 `name="faq-X"` |
| Animation slide | keyframes `accordion-down/up` | `grid-template-rows: 0fr → 1fr` |
| Chevron rotation | `[&[data-state=open]>svg]:rotate-180` | `details[open] .accordion-chevron` |
| Trigger styling | `py-4 font-semibold hover:underline` | identique |
| Border | `border-b border-border` | identique |

Fidélité visuelle : 100%. Fidélité comportementale : 100%. Bonus : 0 JS, accessible clavier + lecteur d'écran par défaut, indexable Google.

---

"""


_COMPONENT_STAGGER_REVEAL = r"""## 9. StaggerReveal — Apparition séquencée des éléments d'une section

**Quand l'utiliser** : sur les sections où plusieurs éléments similaires apparaissent ensemble.
Crée un effet "la section se déploie devant le visiteur" au lieu d'un "ça apparaît tout d'un coup".

**Décision** : **MAX 2 sections par page** avec stagger. Sinon l'effet devient gimmick.
- ✅ Section Services (3-4 cards)
- ✅ Section Réalisations aperçu (4-6 photos)
- ❌ Pas sur Trust bar (trop court)
- ❌ Pas sur Header / Footer (chargés au load, pas à l'entrée)
- ❌ Pas sur FAQ (accordion, autre UX)
- ❌ Pas sur Hero (visible immédiatement)

### Code complet

```html
<!-- Sur la section parente : ajouter data-stagger-parent.
     Sur chaque enfant à animer en séquence : ajouter data-stagger. -->
<section class="services-section py-20" data-stagger-parent>

  <h2 class="text-4xl font-bold mb-12" data-stagger>Nos services</h2>

  <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
    <div class="card" data-stagger>Service 1</div>
    <div class="card" data-stagger>Service 2</div>
    <div class="card" data-stagger>Service 3</div>
  </div>

</section>

<style>
[data-stagger] {
  opacity: 0;
  transform: translateY(20px);
  transition: opacity 0.6s cubic-bezier(0.16, 1, 0.3, 1),
              transform 0.6s cubic-bezier(0.16, 1, 0.3, 1);
}
[data-stagger].is-visible {
  opacity: 1;
  transform: translateY(0);
}

@media (prefers-reduced-motion: reduce) {
  [data-stagger] { opacity: 1; transform: none; transition: none; }
}
</style>

<script>
(function() {
  // Respect reduced-motion : tout visible direct
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    document.querySelectorAll('[data-stagger]').forEach(el => el.classList.add('is-visible'));
    return;
  }

  const obs = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      const children = entry.target.querySelectorAll('[data-stagger]');
      children.forEach((el, i) => {
        el.style.transitionDelay = (i * 80) + 'ms';
        el.classList.add('is-visible');
      });
      obs.unobserve(entry.target);
    });
  }, { threshold: 0.15 });

  document.querySelectorAll('[data-stagger-parent]').forEach(el => obs.observe(el));

  // Fallback Playwright headless : forcer visible apres 2s si pas declenche par scroll
  setTimeout(() => {
    document.querySelectorAll('[data-stagger]:not(.is-visible)').forEach(el => el.classList.add('is-visible'));
  }, 2000);
})();
</script>
```

### Règles d'usage

- **Délai inter-élément : 80ms** (testé optimal). Pas 50ms (trop rapide, on ne perçoit pas
  le stagger), pas 150ms (trop lent, on s'impatiente).
- **Threshold IntersectionObserver : 0.15** (section visible à 15% suffit pour déclencher).
- **Fallback setTimeout(2000) obligatoire** pour Playwright headless / preview_site.py.
- **Easing cubic-bezier(0.16, 1, 0.3, 1)** : courbe "ease-out-expo", arrivée douce et naturelle.

---

"""


_COMPONENT_CURSOR_AWARE_SHADOW = r"""## 10. CursorAwareShadow — Ombre qui suit le curseur sur Service cards

**Quand l'utiliser** : sur les cards interactives importantes (Services principalement).
Effet "carte physiquement suspendue, réagissant à la lumière du curseur".

**Décision** : **uniquement sur les Service cards de la homepage** (3-4 cards max).
Pas partout — sinon perte de spécialité de l'effet.
- ✅ 3-4 cards Services sur index.html
- ❌ Pas sur les photos de galerie (déjà animées au hover)
- ❌ Pas sur mobile (touchscreen, l'effet est invisible et le mousemove ralentit le scroll)
- ❌ Pas sur la FAQ (accordion, autre UX)
- ❌ Pas sur les cards d'avis (déjà animées par TestimonialsColumns)

### Code complet

```html
<!-- Sur chaque card service : classe .card-service -->
<div class="card-service relative bg-white rounded-2xl p-6 transition-shadow duration-300">
  <!-- contenu de la card -->
</div>

<style>
.card-service {
  --shadow-x: 0px;
  --shadow-y: 4px;
  /* Couleur shadow : utilise --color-primary du brand a 12% opacite (pas un noir generique) */
  box-shadow: var(--shadow-x) var(--shadow-y) 40px rgba(28, 87, 98, 0.12);
  transition: box-shadow 200ms ease, transform 300ms cubic-bezier(0.16, 1, 0.3, 1);
}
.card-service:hover {
  transform: translateY(-4px);
}

/* Desactive sur mobile/touch et reduced-motion (effet invisible + ralentit le scroll) */
@media (hover: none), (prefers-reduced-motion: reduce) {
  .card-service { transform: none !important; }
}
</style>

<script>
(function() {
  // Guards : desactive sur touch + reduced-motion
  if (window.matchMedia('(hover: none)').matches) return;
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

  document.querySelectorAll('.card-service').forEach(card => {
    card.addEventListener('mousemove', (e) => {
      const rect = card.getBoundingClientRect();
      const x = ((e.clientX - rect.left) / rect.width  - 0.5);   // -0.5 a 0.5
      const y = ((e.clientY - rect.top)  / rect.height - 0.5);
      // Amplitude max : 16px. Plus = effet exagere, moins premium.
      card.style.setProperty('--shadow-x', (x * 16) + 'px');
      card.style.setProperty('--shadow-y', (y * 16 + 4) + 'px');
    });
    card.addEventListener('mouseleave', () => {
      card.style.setProperty('--shadow-x', '0px');
      card.style.setProperty('--shadow-y', '4px');
    });
  });
})();
</script>
```

### Règles d'usage

- **Amplitude shadow : 16px max** (`x * 16`). Plus = effet exagéré, moins premium.
- **Couleur shadow : couleur primary du brand à ~12% opacité**, pas un noir générique
  (`rgba(0,0,0,0.1)` = cheap). Remplacer `rgba(28, 87, 98, 0.12)` par les valeurs RGB
  de ton `--color-primary` du DESIGN.md.
- **Désactivation tactile non négociable** : sur mobile l'effet est invisible et le
  `mousemove` handler crée du jank au scroll.
- **À utiliser SEULEMENT** sur les 3-4 Service cards de la homepage. Pas partout.

---

"""


def generate_components_file(out_dir: Path, n_reviews_text: int = 0) -> None:
    """
    Génère COMPONENTS.md dans le dossier profil du client.
    Claude Code lit ce fichier et intègre les composants intelligemment selon le layout.

    n_reviews_text : nombre d'avis avec du texte (conditionne la recommandation TestimonialsColumns)
    """
    # Adapter la note sur TestimonialsColumns selon le volume d'avis
    testimonials_block = _COMPONENT_TESTIMONIALS
    if n_reviews_text < 8:
        testimonials_block = testimonials_block.replace(
            "**Décision** : Utiliser si ≥ 8 avis texte disponibles.",
            f"**Décision** : ⚠️ Ce profil a {n_reviews_text} avis texte (seuil = 8)."
            " Utiliser SimpleReviewCarousel (§7) à la place.",
        )

    content = (
        _COMPONENTS_INTRO
        + _COMPONENT_NUMBER_TICKER
        + _COMPONENT_TEXT_HIGHLIGHTER
        + _COMPONENT_IMAGE_GALLERY
        + _COMPONENT_BEFORE_AFTER
        + _COMPONENT_NAV_DESKTOP
        + testimonials_block
        + _COMPONENT_SIMPLE_CAROUSEL
        + _COMPONENT_FAQ_ACCORDION
        + _COMPONENT_STAGGER_REVEAL
        + _COMPONENT_CURSOR_AWARE_SHADOW
    )

    out_path = out_dir / "COMPONENTS.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(content)
    tsc_note = "OK" if n_reviews_text >= 8 else f"sous seuil ({n_reviews_text}/8 -> SimpleCarousel)"
    print(f"    COMPONENTS.md  -- 10 composants vanilla (TestimonialsColumns: {tsc_note})")


# ─────────────────────────────────────────────────────────────
# AUDIT CONTENU — détection hallucinations post-build
# ─────────────────────────────────────────────────────────────

_AUDIT_CONTENT_SCRIPT = r'''"""
audit_content.py — Detecte les hallucinations dans les HTML generes.

Confronte chaque fait specifique (date, prix, duree, nom propre) trouve dans site/*.html
aux donnees sources brutes : profil.json, reviews_all.json, reviews_meta.json.
Pas de pre-extraction : recherche directe par substring (le script ne fait que chercher).

Usage : python audit_content.py
Output : HALLUCINATIONS_REPORT.md a la racine du workspace.
"""

import json, re, sys
from pathlib import Path
from datetime import datetime

ROOT     = Path(__file__).parent
SITE_DIR = ROOT / "site"

# Patterns de detection
PATTERNS = {
    "date":       re.compile(r"\b(19[0-9]{2}|20[0-2][0-9])\b"),
    "price":      re.compile(r"\b\d{1,3}(?:[\s.,]\d{3})*\s*(?:€|euros?|EUR)\b", re.IGNORECASE),
    "duration":   re.compile(r"\b\d+\s*(ans?|mois|semaines?|jours?|heures?|minutes?)\b", re.IGNORECASE),
    "percent":    re.compile(r"\b\d+(?:[.,]\d+)?\s*%"),
    "warranty":   re.compile(r"\b(garantie|d[ée]cennale|assurance)\b", re.IGNORECASE),
    "tenure":     re.compile(r"\b(depuis\s+\d+|fond[ée]\s+en|cr[ée]{2}\s+en|install[ée]\s+depuis)\b", re.IGNORECASE),
    "name":       re.compile(r"\b([A-ZÀ-Ÿ][a-zà-ÿ]{2,15})\b"),
}

# Mots qui sont systematiquement OK meme s'ils sont detectes (noms communs, mois, etc.)
NAME_WHITELIST = {
    # Mois / jours
    "Janvier","Fevrier","Mars","Avril","Mai","Juin","Juillet","Aout","Septembre",
    "Octobre","Novembre","Decembre","Lundi","Mardi","Mercredi","Jeudi","Vendredi",
    "Samedi","Dimanche",
    # Pays / villes courantes
    "France","Paris","Lyon","Marseille","Reims","Nice","Toulouse","Bordeaux","Lille",
    "Nantes","Strasbourg","Rennes","Montpellier","Dijon","Angers","Le","La","Les",
    # Tech
    "Google","Vercel","Tailwind","Alpine","Material","Symbols","HTML","CSS","JS",
    # Sections / nav
    "Mentions","Politique","Devis","Accueil","Services","Contact","Cookies",
    "Confidentialite","Confidentialité","Realisations","Réalisations","Propos",
    "Description","Accroche","Notre","Nos","Vos","Vous","Nous","Mon","Ma","Mes","Ton","Ta","Tes",
    "Son","Sa","Ses","Leur","Leurs","Cette","Cet","Ce","Tous","Toutes","Tout","Toute",
    "Avis","Note","Etoiles","Étoiles","Star","Stars",
    # Mots métier
    "Plomberie","Chauffage","Electricite","Électricité","Couverture","Toiture","Peinture",
    "Decoration","Décoration","Jardin","Paysagisme","Serrurerie","Climatisation","Menuiserie",
    "Carrelage","Demenagement","Déménagement","Nettoyage","Batiment","Bâtiment",
    "Construction","Renovation","Rénovation","Installation","Depannage","Dépannage",
    "Maintenance","Entretien","Reparation","Réparation","Travaux","Chantier","Projet",
    "Equipe","Équipe","Artisan","Pro","Professionnel","Expert","Specialiste","Spécialiste",
    # Garanties/légal/commerce (déjà flagués par d'autres patterns)
    "Garantie","Garanties","Decennale","Décennale","Assurance","Devis","Tarif","Tarifs",
    "Prix","Estimation","Budget","Forfait","Formule","Pack","Offre","Promotion",
    # Verbes/adj courants en début de phrase
    "Avec","Sans","Pour","Apres","Après","Avant","Dans","Sur","Sous","Entre",
    "Bonjour","Bonsoir","Merci","Bienvenue","Decouvrez","Découvrez","Trouvez",
    "Demandez","Contactez","Appelez","Envoyez","Recevez","Beneficier","Bénéficier",
    "Voir","Lire","Visiter","Explorer","Reserver","Réserver",
    # Réseaux sociaux
    "Facebook","Instagram","TikTok","LinkedIn","YouTube","Twitter","WhatsApp",
}


def load_sources():
    """Charge profil.json, reviews_all.json, reviews_meta.json en un texte unique."""
    sources = {"profil": "", "reviews": "", "meta": ""}
    for key, fname in [("profil","profil.json"), ("reviews","reviews_all.json"),
                       ("meta","site/reviews_meta.json")]:
        p = ROOT / fname
        if p.exists():
            sources[key] = p.read_text(encoding="utf-8", errors="ignore")
    return sources


def _normalize(text: str) -> str:
    """Lowercase + retire les accents pour recherche tolerante."""
    import unicodedata
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return text.lower()


def fact_in_source(fact: str, sources: dict) -> str:
    """Retourne 'profil', 'reviews', 'meta' ou '' si trouve (insensible accents/casse)."""
    needle = _normalize(fact).strip()
    if not needle:
        return ""
    for key in ("meta", "profil", "reviews"):
        # Cache la version normalisee de chaque source pour eviter de la recalculer N fois
        cache_key = f"_norm_{key}"
        if cache_key not in sources:
            sources[cache_key] = _normalize(sources[key])
        if needle in sources[cache_key]:
            return key
    return ""


def strip_html(html: str) -> str:
    """Retire les balises et garde le texte + commentaires HTML."""
    # Garder le contenu mais retirer scripts/styles entiers
    html = re.sub(r"<script[^>]*>.*?</script>", " ", html, flags=re.S | re.I)
    html = re.sub(r"<style[^>]*>.*?</style>",   " ", html, flags=re.S | re.I)
    # Decoder les entites courantes
    html = html.replace("&nbsp;", " ").replace("&amp;", "&")
    html = html.replace("&euro;", "€").replace("&#8364;", "€")
    html = html.replace("&apos;", "'").replace("&#039;", "'")
    html = html.replace("&quot;", '"')
    # Retirer toutes les balises
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text)


def get_line_num(text: str, pos: int) -> int:
    return text[:pos].count("\n") + 1


def audit_page(html_path: Path, sources: dict) -> dict:
    """Retourne dict {ok: [...], warn: [...], err: [...]} pour cette page."""
    html_raw = html_path.read_text(encoding="utf-8", errors="ignore")
    text = strip_html(html_raw)

    findings = {"ok": [], "warn": [], "err": []}

    # Dates 19XX / 20XX
    for m in PATTERNS["date"].finditer(text):
        year = m.group(0)
        src = fact_in_source(year, sources)
        ctx = text[max(0, m.start()-30):m.end()+30].strip()
        item = {"type": "date", "value": year, "context": ctx}
        if src:
            findings["ok"].append({**item, "source": src})
        else:
            findings["err"].append(item)

    # Prix
    for m in PATTERNS["price"].finditer(text):
        price = m.group(0)
        src = fact_in_source(price, sources) or fact_in_source(price.replace(" ",""), sources)
        ctx = text[max(0, m.start()-30):m.end()+30].strip()
        item = {"type": "price", "value": price, "context": ctx}
        if src:
            findings["ok"].append({**item, "source": src})
        else:
            findings["err"].append(item)

    # Durees "X ans/mois/jours" — JAUNE par defaut (peut etre chrono ou garantie)
    for m in PATTERNS["duration"].finditer(text):
        dur = m.group(0)
        src = fact_in_source(dur, sources)
        ctx = text[max(0, m.start()-30):m.end()+30].strip()
        item = {"type": "duration", "value": dur, "context": ctx}
        # Si entoure de "garantie" / "depuis" / "experience" → suspect direct
        ctx_low = ctx.lower()
        is_suspect = any(w in ctx_low for w in ("garantie","depuis","exp","ancien","fond"))
        if src:
            findings["ok"].append({**item, "source": src})
        elif is_suspect:
            findings["err"].append(item)
        else:
            findings["warn"].append(item)

    # Pourcentages
    for m in PATTERNS["percent"].finditer(text):
        pct = m.group(0)
        src = fact_in_source(pct, sources)
        ctx = text[max(0, m.start()-30):m.end()+30].strip()
        item = {"type": "percent", "value": pct, "context": ctx}
        if src:
            findings["ok"].append({**item, "source": src})
        else:
            findings["err"].append(item)

    # Mentions garantie/decennale — toujours suspect si non source
    for m in PATTERNS["warranty"].finditer(text):
        word = m.group(0)
        ctx = text[max(0, m.start()-40):m.end()+40].strip()
        # Verifier si "garantie"/"decennale"/"assurance" apparait dans les avis (insensible accents)
        reviews_norm = _normalize(sources["reviews"])
        in_reviews = any(w in reviews_norm for w in ("garantie","decennale","assurance"))
        item = {"type": "warranty_mention", "value": word, "context": ctx}
        if in_reviews:
            findings["warn"].append({**item, "source": "reviews",
                "note": "garantie mentionnee dans les avis — verifier que la mention dans le site cite la source"})
        else:
            findings["err"].append({**item,
                "note": "aucune garantie/decennale/assurance dans les avis — interdiction d'affirmer"})

    # Mentions de tenure ("depuis X", "fonde en")
    for m in PATTERNS["tenure"].finditer(text):
        phrase = m.group(0)
        src = fact_in_source(phrase, sources)
        ctx = text[max(0, m.start()-30):m.end()+30].strip()
        item = {"type": "tenure", "value": phrase, "context": ctx}
        if src:
            findings["warn"].append({**item, "source": src, "note": "vérifier la véracité de la date"})
        else:
            findings["err"].append(item)

    # Noms propres — recherche litterale dans profil + reviews
    seen_names = set()
    for m in PATTERNS["name"].finditer(text):
        name = m.group(0)
        if name in NAME_WHITELIST or name in seen_names:
            continue
        seen_names.add(name)
        src = fact_in_source(name, sources)
        ctx = text[max(0, m.start()-30):m.end()+30].strip()
        item = {"type": "name", "value": name, "context": ctx}
        if src:
            # Trouve litteralement dans profil/reviews/meta : OK silencieux (pas de spam)
            pass
        else:
            findings["err"].append(item)

    return findings


def main():
    if not SITE_DIR.exists():
        print(f"ERREUR : {SITE_DIR} introuvable. Claude n'a pas encore genere le site.")
        sys.exit(1)

    html_files = sorted(SITE_DIR.glob("*.html"))
    if not html_files:
        print(f"ERREUR : aucun .html dans {SITE_DIR}/")
        sys.exit(1)

    sources = load_sources()
    if not sources["reviews"]:
        print("ATTENTION : reviews_all.json introuvable — audit basique sans corpus avis")

    lines = []
    lines.append(f"# Audit contenu — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append("Confronte les faits spécifiques du HTML aux données sources.")
    lines.append("Régles : `[OK]` source vérifiée. `[?]` ambiguë, à vérifier. `[X]` absent partout = hallucination probable.")
    lines.append("")

    total_ok = total_warn = total_err = 0

    for html in html_files:
        f = audit_page(html, sources)
        n_ok, n_warn, n_err = len(f["ok"]), len(f["warn"]), len(f["err"])
        total_ok += n_ok; total_warn += n_warn; total_err += n_err
        if n_ok == 0 and n_warn == 0 and n_err == 0:
            continue
        lines.append(f"## {html.name}")
        lines.append("")
        if n_err:
            lines.append(f"### [X] Hallucinations probables ({n_err})")
            for item in f["err"]:
                lines.append(f"- **{item['type']}** : `{item['value']}` -- _\"{item['context']}\"_")
                if item.get("note"):
                    lines.append(f"  > {item['note']}")
            lines.append("")
        if n_warn:
            lines.append(f"### [?] Ambiguites ({n_warn})")
            for item in f["warn"]:
                lines.append(f"- **{item['type']}** : `{item['value']}` -- _\"{item['context']}\"_")
                if item.get("note"):
                    lines.append(f"  > {item['note']}")
            lines.append("")
        if n_ok:
            lines.append(f"### [OK] Faits verifies ({n_ok})")
            for item in f["ok"][:15]:
                lines.append(f"- **{item['type']}** : `{item['value']}` (source: {item['source']})")
            if n_ok > 15:
                lines.append(f"- _...et {n_ok-15} autres_")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"## Total : {total_ok} OK | {total_warn} ambigus | {total_err} hallucinations probables")
    lines.append("")
    if total_err > 0:
        lines.append(f"**CORRIGER LES {total_err} HALLUCINATIONS AVANT DEPLOY.**")
        lines.append("")
        lines.append("Pour chaque [X] : soit supprimer le fait, soit citer la vraie source, soit passer en formulation generique honnete (Mode 2).")
    else:
        lines.append("**RAS — pret pour la PHASE 6 deploy.**")

    out = ROOT / "HALLUCINATIONS_REPORT.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nRapport : {out}")
    print(f"  [OK] {total_ok}   [?] {total_warn}   [X] {total_err}")
    sys.exit(1 if total_err > 0 else 0)


if __name__ == "__main__":
    main()
'''


def generate_audit_script(out_dir: Path) -> None:
    """Dépose audit_content.py dans le workspace."""
    (out_dir / "audit_content.py").write_text(_AUDIT_CONTENT_SCRIPT, encoding="utf-8")
    print(f"    audit_content.py -- detecte les hallucinations post-build")


# ─────────────────────────────────────────────────────────────
# OPTIMISATION IMAGES — WebP en complément des JPG
# ─────────────────────────────────────────────────────────────

def generate_webp_variants(photos_dir: Path, quality: int = 85) -> int:
    """
    Pour chaque .jpg dans photos_dir, génère un .webp à côté.
    Retourne le nombre de WebP créés. Skippe ceux déjà présents.
    """
    try:
        from PIL import Image
    except ImportError:
        print("    Pillow non disponible — skip WebP (pip install Pillow)")
        return 0

    count = 0
    for jpg in sorted(photos_dir.glob("*.jpg")) + sorted(photos_dir.glob("*.jpeg")):
        webp = jpg.with_suffix(".webp")
        if webp.exists():
            continue
        try:
            img = Image.open(jpg)
            if img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(webp, "WEBP", quality=quality, method=6)
            count += 1
        except Exception as e:
            print(f"    WebP {jpg.name} erreur : {e}")
    if count:
        print(f"    {count} variants WebP générés (quality={quality})")
    return count


# ─────────────────────────────────────────────────────────────
# SEO FILES — sitemap.xml + robots.txt
# ─────────────────────────────────────────────────────────────

def generate_seo_files(out_dir: Path, site_url_placeholder: str = "__SITE_URL__") -> None:
    """
    Génère sitemap.xml et robots.txt dans le dossier profil.
    L'URL de base est un placeholder — Claude Code doit la remplacer après deploy Vercel.
    """
    pages = [
        ("", "1.0"),
        ("services.html", "0.9"),
        ("realisations.html", "0.9"),
        ("a-propos.html", "0.7"),
        ("contact.html", "0.9"),
        ("mentions-legales.html", "0.3"),
    ]
    today = datetime.now().strftime("%Y-%m-%d")

    sitemap_xml = '<?xml version="1.0" encoding="UTF-8"?>\n'
    sitemap_xml += '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    for path, prio in pages:
        sitemap_xml += "  <url>\n"
        sitemap_xml += f"    <loc>{site_url_placeholder}/{path}</loc>\n"
        sitemap_xml += f"    <lastmod>{today}</lastmod>\n"
        sitemap_xml += f"    <priority>{prio}</priority>\n"
        sitemap_xml += "  </url>\n"
    sitemap_xml += "</urlset>\n"

    robots = (
        "User-agent: *\n"
        "Allow: /\n"
        f"Sitemap: {site_url_placeholder}/sitemap.xml\n"
    )

    (out_dir / "sitemap.xml").write_text(sitemap_xml, encoding="utf-8")
    (out_dir / "robots.txt").write_text(robots, encoding="utf-8")
    print(f"    sitemap.xml + robots.txt -- marqueur URL : {site_url_placeholder} (a remplacer via finalize_site.py)")


# ─────────────────────────────────────────────────────────────
# PREVIEW SITE — capture screenshots pour PHASE 4.5 REGARD
# ─────────────────────────────────────────────────────────────

_PREVIEW_SITE_SCRIPT = r'''"""
preview_site.py — Capture des screenshots de chaque page HTML du site
pour la PHASE 4.5 REGARD (jugement visuel).

Sert le sous-dossier ./site/ via HTTP local, et screenshote chaque .html
en mobile (390x844) ET desktop (1440x900).

Utilisation :
    python preview_site.py            # capture toutes les pages
    python preview_site.py index      # capture seulement index.html

Output : ./preview/{page}_mobile.png et ./preview/{page}_desktop.png
"""

import sys, time, threading, http.server, socketserver
from pathlib import Path

PORT     = 8765
ROOT     = Path(__file__).parent
SITE_DIR = ROOT / "site"

VIEWPORTS = {
    "mobile":  {"width": 390,  "height": 844},
    "desktop": {"width": 1440, "height": 900},
}


def start_server():
    """Sert le dossier site/ sur localhost:PORT en thread daemon."""
    import os
    if not SITE_DIR.exists():
        print(f"ERREUR: {SITE_DIR} introuvable. Claude doit d'abord ecrire les .html dans site/.")
        sys.exit(1)
    os.chdir(str(SITE_DIR))
    handler = http.server.SimpleHTTPRequestHandler
    handler.log_message = lambda *a, **k: None
    httpd = socketserver.TCPServer(("127.0.0.1", PORT), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd


def capture(pages=None):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERREUR: playwright non installe -> pip install playwright && playwright install chromium")
        sys.exit(1)

    preview_dir = ROOT / "preview"
    preview_dir.mkdir(exist_ok=True)

    if pages is None:
        pages = [p.stem for p in SITE_DIR.glob("*.html") if p.name != "merci.html"]
        if not pages:
            print(f"ERREUR: aucun .html trouve dans {SITE_DIR}/. Claude n'a pas encore genere le site.")
            sys.exit(1)

    print(f"Demarrage serveur sur http://localhost:{PORT}/")
    httpd = start_server()
    time.sleep(0.5)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            for page_name in pages:
                for vp_name, vp in VIEWPORTS.items():
                    ctx = browser.new_context(viewport=vp)
                    page = ctx.new_page()
                    url = f"http://localhost:{PORT}/{page_name}.html"
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=15000)
                        # Scroll jusqu'en bas puis remonter pour activer tous les IntersectionObservers
                        # (.reveal, .gallery-img, etc.). Sans ca les sections sous le fold sont blanches.
                        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                        time.sleep(0.6)
                        page.evaluate("window.scrollTo(0, 0)")
                        time.sleep(0.4)
                        # Verification des images cassees + liens orphelins
                        broken = page.evaluate("""() => {
                            const imgs = [...document.querySelectorAll('img')]
                                .filter(i => i.complete && i.naturalWidth === 0)
                                .map(i => i.getAttribute('src'));
                            const links = [...document.querySelectorAll('a[href="#"], a[href=""]')]
                                .map(a => (a.textContent||'').trim().slice(0,40));
                            return {imgs: imgs, links: links};
                        }""")
                        if broken.get('imgs'):
                            print(f"    WARN  {page_name}_{vp_name} - images cassees: {broken['imgs'][:3]}")
                        if broken.get('links'):
                            print(f"    WARN  {page_name}_{vp_name} - liens orphelins href='#': {broken['links'][:3]}")
                        out = preview_dir / f"{page_name}_{vp_name}.png"
                        page.screenshot(path=str(out), full_page=True)
                        print(f"  OK  {out.relative_to(ROOT)}  ({vp['width']}x{vp['height']})")
                    except Exception as e:
                        print(f"  FAIL  {page_name}_{vp_name}: {e}")
                    ctx.close()
            browser.close()
    finally:
        httpd.shutdown()

    print(f"\nFait. Screenshots dans : {preview_dir}/")
    print("A lire avec le tool Read pour la PHASE 4.5 REGARD.")


if __name__ == "__main__":
    pages_arg = sys.argv[1:] if len(sys.argv) > 1 else None
    capture(pages_arg)
'''


def generate_preview_script(out_dir: Path) -> None:
    """Dépose preview_site.py dans le dossier profil."""
    (out_dir / "preview_site.py").write_text(_PREVIEW_SITE_SCRIPT, encoding="utf-8")
    print(f"    preview_site.py -- capture screenshots pour QC visuel")


# ─────────────────────────────────────────────────────────────
# FINALIZE_SITE — remplace les marqueurs __SITE_URL__ et __CLIENT_EMAIL__
# ─────────────────────────────────────────────────────────────

_FINALIZE_SITE_SCRIPT = r'''"""
finalize_site.py — Remplace les marqueurs __SITE_URL__ et __CLIENT_EMAIL__
par les vraies valeurs dans tous les fichiers de site/.

Re-executable : utilise .finalize_state.json pour tracker les dernieres valeurs injectees.
A chaque execution, remplace les marqueurs ET les anciennes valeurs par les nouvelles.

Utilisation (typiquement 2 moments dans le workflow) :

  1. Apres le 1er deploy "blind" (pour la demo client) :
     python finalize_site.py --url https://h2o-plomberie-abc123.vercel.app --email paveau.romain@gmail.com

  2. Apres la vente, quand le client a son custom domain :
     python finalize_site.py --url https://h2o-plomberie.fr --email contact@h2o-plomberie.fr
     → Remplace correctement l ancienne URL Vercel par le custom domain
       et l ancien email par celui du client.

Apres chaque appel, redeploy : cd site && vercel --prod
"""

import argparse, json, sys
from pathlib import Path

ROOT = Path(__file__).parent
SITE = ROOT / "site"
STATE_FILE = ROOT / ".finalize_state.json"

MARKERS = ("__SITE_URL__", "__CLIENT_EMAIL__")
EXTENSIONS = (".html", ".xml", ".txt", ".json", ".js", ".webmanifest")


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_state(url: str, email: str) -> None:
    STATE_FILE.write_text(
        json.dumps({"url": url, "email": email}, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def main():
    parser = argparse.ArgumentParser(description="Remplace marqueurs + anciennes valeurs dans site/")
    parser.add_argument("--url", required=True,
        help="URL finale du site (ex: https://h2o-plomberie.fr ou https://xxx.vercel.app)")
    parser.add_argument("--email", required=True,
        help="Email destinataire des devis (FormSubmit)")
    args = parser.parse_args()

    url = args.url.rstrip("/")
    if not url.startswith("http"):
        print(f"[X] URL doit commencer par http(s):// : {url}")
        sys.exit(1)

    email = args.email.strip()
    if "@" not in email:
        print(f"[X] Email invalide : {email}")
        sys.exit(1)

    if not SITE.exists():
        print(f"[X] {SITE} introuvable. Claude n a pas encore genere le site.")
        sys.exit(1)

    # Construire la map de remplacements :
    # 1. Marqueurs (au 1er run) -> nouvelles valeurs
    # 2. Anciennes valeurs (state) -> nouvelles valeurs (pour re-executions)
    state = load_state()
    replacements = {
        "__SITE_URL__":     url,
        "__CLIENT_EMAIL__": email,
    }
    old_url = state.get("url")
    old_email = state.get("email")
    if old_url and old_url != url:
        replacements[old_url] = url
    if old_email and old_email != email:
        replacements[old_email] = email

    files_modified = 0
    total_replacements = 0

    for f in SITE.rglob("*"):
        if not f.is_file() or f.suffix not in EXTENSIONS:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue

        original = text
        for old, new in replacements.items():
            count = text.count(old)
            if count > 0:
                text = text.replace(old, new)
                total_replacements += count

        if text != original:
            f.write_text(text, encoding="utf-8")
            files_modified += 1

    save_state(url, email)

    print()
    print(f"[OK] {files_modified} fichiers modifies, {total_replacements} remplacements effectues")
    print(f"     URL   : {url}")
    print(f"     Email : {email}")
    if old_url and old_url != url:
        print(f"     (ancienne URL remplacee : {old_url})")
    if old_email and old_email != email:
        print(f"     (ancien email remplace : {old_email})")
    print()

    # Verification finale qu il ne reste plus de marqueurs
    remaining = []
    for f in SITE.rglob("*"):
        if not f.is_file() or f.suffix not in EXTENSIONS:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except Exception:
            continue
        for marker in MARKERS:
            if marker in text:
                remaining.append(f"{f.relative_to(ROOT)} : {marker}")

    if remaining:
        print("[?] ATTENTION : marqueurs encore presents :")
        for m in remaining:
            print(f"     {m}")
        sys.exit(1)

    print("Prochaine etape : cd site && vercel --prod")


if __name__ == "__main__":
    main()
'''


def generate_finalize_script(out_dir: Path) -> None:
    """Dépose finalize_site.py dans le dossier profil."""
    (out_dir / "finalize_site.py").write_text(_FINALIZE_SITE_SCRIPT, encoding="utf-8")
    print(f"    finalize_site.py -- remplace __SITE_URL__ et __CLIENT_EMAIL__ avant deploy")


# ─────────────────────────────────────────────────────────────
# POST_DEPLOY.md — checklist demo + vente + custom domain + maintenance
# ─────────────────────────────────────────────────────────────

_POST_DEPLOY_TEMPLATE = """# POST_DEPLOY.md — Workflow apres construction du site

Le site est construit par Claude. Les fichiers contiennent encore les marqueurs `__SITE_URL__`
et `__CLIENT_EMAIL__`. Voici les etapes apres construction.

---

## 1. 1er deploy "blind" (pour la demo client)

```bash
cd site && vercel --prod
```

Vercel retourne l URL auto, ex : `https://{nom_projet}-abc123.vercel.app`. Note cette URL.

---

## 2. Remplacer les marqueurs pour la phase demo

```bash
python finalize_site.py --url https://{nom_projet}-abc123.vercel.app --email TON_EMAIL@gmail.com
```

L email peut etre le tien pendant la demo (les tests t arrivent, OK pour montrer au client).

Re-deploy :
```bash
cd site && vercel --prod
```

Le site demo est pret. URL a montrer au client : `https://{nom_projet}-abc123.vercel.app`

---

## 3. Appel client + vente + choix du nom de domaine

Pendant l appel, le client choisit son nom de domaine (ex: `h2o-plomberie.fr`).

Si le client n a pas encore le domaine :
- Lui faire acheter chez OVH / Gandi / Namecheap (≤ 20€/an)
- Recuperer les acces du registrar

---

## 4. Configurer le custom domain dans Vercel

1. Vercel dashboard → Project → Settings → Domains
2. **Add Domain** → entrer `h2o-plomberie.fr` (et eventuellement `www.h2o-plomberie.fr`)
3. Vercel affiche les records DNS a configurer :
   - Domaine racine : `A` record vers `76.76.21.21`
   - Sous-domaine www : `CNAME` vers `cname.vercel-dns.com`
4. Aller chez le registrar du client, DNS Zone → ajouter ces records
5. Attendre la propagation DNS (10 min a 24h selon le registrar)
6. Vercel auto-active SSL Let s Encrypt (quelques minutes apres propagation)

---

## 5. Mettre a jour le site avec le custom domain + email client

```bash
python finalize_site.py --url https://h2o-plomberie.fr --email contact@h2o-plomberie.fr
```

Re-deploy :
```bash
cd site && vercel --prod
```

Le site final est live sur `https://h2o-plomberie.fr`.

---

## 6. ⚠️ ACTIVATION FORMSUBMIT (CRITIQUE — a faire avant de livrer)

FormSubmit.co exige une activation manuelle a la 1ere soumission. Tant que pas faite,
**aucun devis n est livre** au client.

> ⚠️ **L activation est par adresse email destinataire**. Si tu as deja active pendant la phase
> demo (avec ton propre email type `paveau.romain@gmail.com`), il faut **RE-ACTIVER** apres
> avoir change pour l email du client a l etape 5. FormSubmit considere chaque combinaison
> (URL site, email destinataire) comme un nouveau form.

1. Ouvrir `https://h2o-plomberie.fr/contact.html` (ou la page contact)
2. Soumettre un VRAI test (pas du lorem ipsum — FormSubmit detecte le spam)
3. Aller dans la boite email du client : `contact@h2o-plomberie.fr`
4. Trouver l email de FormSubmit (sujet "Confirm Email")
5. **CLIQUER LE LIEN D ACTIVATION**
6. Soumettre un 2eme test depuis le site
7. Verifier que ce 2eme test arrive bien dans la boite du client

---

## 7. Checklist avant livraison

- [ ] URL en HTTPS (cadenas vert dans navigateur)
- [ ] Toutes les pages chargent (menu : Accueil, Services, Realisations, A propos, Contact)
- [ ] Favicon visible dans l onglet
- [ ] Soumission test form OK + email recu chez le client (cf §6)
- [ ] Tester sur un vrai telephone mobile (pas juste responsive mode)
  - [ ] Bottom nav visible et fonctionnelle
  - [ ] Telephone cliquable (lien tel:)
  - [ ] Form lisible et soumettable
- [ ] Sitemap accessible : `https://h2o-plomberie.fr/sitemap.xml` retourne du XML
- [ ] Photos chargent en WebP (DevTools Network → filter "webp")
- [ ] Lighthouse mobile ≥ 90 (DevTools → Lighthouse → Mobile)

Si tout est coche → livrer au client avec l URL finale.

---

## 8. Maintenance post-livraison

Le scraper Python n est PAS concu pour la maintenance. Pour modifier le site apres livraison :

### Modifications legeres (telephone, horaires, services)
- Editer directement les fichiers HTML dans `site/`
- ⚠️ Le telephone apparait dans : header + footer + JSON-LD + bottom nav (presque toutes les pages)
- Utiliser un find-replace sur tout le dossier `site/`
- Redeployer : `cd site && vercel --prod`

### Ajout d une page ou refonte d une section
- Demander a Claude Code, workspace = `profils/{nom_artisan}/`
- Lui dire de respecter le DESIGN.md existant
- Verifier visuellement avec `python preview_site.py`
- Redeployer : `cd site && vercel --prod`

### Re-extraction des donnees Google (nouvelles photos, nouveaux avis)
- ⚠️ Re-lancer `company_scraper_bis.py --force` **ecrase tout le travail Claude existant**
- Solution : avant re-scraping, faire un backup du dossier `site/`
- Apres re-scraping, copier les HTML de l ancien `site/` dans le nouveau
- Re-lancer `python finalize_site.py --url ... --email ...`
- Redeployer
"""


def generate_post_deploy_doc(out_dir: Path) -> None:
    """Dépose POST_DEPLOY.md dans le workspace."""
    (out_dir / "POST_DEPLOY.md").write_text(_POST_DEPLOY_TEMPLATE, encoding="utf-8")
    print(f"    POST_DEPLOY.md -- workflow deploy + activation FormSubmit + custom domain + maintenance")


# ─────────────────────────────────────────────────────────────
# PIPELINE PRINCIPAL
# ─────────────────────────────────────────────────────────────

def process(place_id: str, api_key: str, force: bool = False):
    details = api_details(place_id, api_key)
    if not details:
        return None

    name      = details.get("name", place_id)
    out_dir   = OUTPUT_DIR / safe_dirname(name)     # WORKSPACE (CLAUDE.md, etc.)
    site_dir  = out_dir / "site"                    # DEPLOYABLE (HTML, assets)
    photo_dir = site_dir / "photos"
    video_dir = site_dir / "videos"

    if out_dir.exists() and not force:
        print(f"  Deja traite : {out_dir}  (--force pour ecraser)")
        return out_dir

    out_dir.mkdir(parents=True, exist_ok=True)
    site_dir.mkdir(parents=True, exist_ok=True)
    video_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n  {name}")
    print(f"  {details.get('formatted_address','')[:70]}")
    print(f"  {details.get('rating',0)}/5  ({details.get('user_ratings_total',0)} avis)  |  Site : {details.get('website','aucun')}")

    # 1. Photos API → site/photos/
    api_photo_refs = details.get("photos", [])
    print(f"\n  [1/4] Photos API ({len(api_photo_refs)} references)...")
    api_photos = api_download_photos(api_photo_refs, photo_dir, api_key)

    # 2. Scraping Maps — avis + photos supplementaires
    maps_url = details.get("url", "")
    scraped_reviews = []
    scraped_photos  = []
    if maps_url:
        print(f"\n  [2/4] Scraping Google Maps (avis + photos)...")
        scraped_photos, scraped_reviews = scrape_maps(maps_url, photo_dir, len(api_photos))
    else:
        print(f"\n  [2/4] URL Maps absente — scraping ignore")

    all_photos = api_photos + scraped_photos

    # 3. Fusion avis
    print(f"\n  [3/4] Fusion et deduplication des avis...")
    all_reviews = merge_reviews(details.get("reviews", []), scraped_reviews)
    print(f"    API: {len(details.get('reviews',[]))}  Maps: {len(scraped_reviews)}  Final apres dedup: {len(all_reviews)}")

    # Extraction précoce — nécessaire pour favicon (couleur par métier)
    _types = [t for t in details.get("types", [])
              if t not in ("point_of_interest", "establishment")]

    # 4. Génération des fichiers
    print(f"\n  [4/4] Generation fichiers workflow Claude Code...")

    # --- WORKSPACE (out_dir) : fichiers pour Claude Code, pas deployes ---
    with open(out_dir / "profil.json", "w", encoding="utf-8") as f:
        json.dump({**details, "scraped_reviews": scraped_reviews}, f, ensure_ascii=False, indent=2)

    with open(out_dir / "profil.txt", "w", encoding="utf-8") as f:
        f.write(build_profile_txt(details, all_reviews, all_photos))

    with open(out_dir / "reviews_all.json", "w", encoding="utf-8") as f:
        json.dump(all_reviews, f, ensure_ascii=False, indent=2)
    print(f"    reviews_all.json -- {len(all_reviews)} avis bruts (workspace, non deploye)")

    # audit_content.py (workspace) — script de détection des hallucinations post-build
    generate_audit_script(out_dir)

    # PRODUCT.md (workspace)
    product_md_content = build_product_md(details, all_photos, out_dir)
    with open(out_dir / "PRODUCT.md", "w", encoding="utf-8") as f:
        f.write(product_md_content)
    print(f"    PRODUCT.md     -- contexte metier pour skill impeccable")

    # COMPONENTS.md (workspace)
    _n_reviews_text = sum(1 for r in all_reviews if len(r.get("text", "").strip()) > 30)
    generate_components_file(out_dir, n_reviews_text=_n_reviews_text)

    # preview_site.py (workspace, sert site/)
    generate_preview_script(out_dir)

    # finalize_site.py (workspace, remplace __SITE_URL__ et __CLIENT_EMAIL__ apres deploy)
    generate_finalize_script(out_dir)

    # POST_DEPLOY.md (workspace, workflow deploy demo -> vente -> domain -> FormSubmit)
    generate_post_deploy_doc(out_dir)

    # --- SITE/ (site_dir) : fichiers deployables ---
    # reviews.json (top 12, format frontend)
    exported_reviews = generate_reviews_json(all_reviews, site_dir)

    # reviews_meta.json (compteurs)
    generate_reviews_meta_json(details, site_dir)

    # reviews_inline.js (embed, evite fetch async)
    meta = {
        "rating": details.get("rating", 0),
        "count": details.get("user_ratings_total", 0),
        "place_id": details.get("place_id", ""),
        "review_url": (f"https://search.google.com/local/writereview?placeid={details.get('place_id','')}"
                       if details.get("place_id") else ""),
    }
    generate_reviews_inline_js(exported_reviews, meta, site_dir)

    # favicon.svg
    generate_favicon(name, site_dir, types=_types, design_lookup_dir=out_dir)

    # WebP variants
    generate_webp_variants(photo_dir)

    # sitemap.xml + robots.txt
    generate_seo_files(site_dir)

    # CLAUDE.md (workspace — en DERNIER pour que tous les fichiers existent quand il est ecrit)
    build_claude_md(details, all_reviews, all_photos, out_dir)

    # Resume
    print(f"\n  {'=' * 55}")
    print(f"  OK  {name}")
    print(f"  {'=' * 55}")
    print(f"    Photos         : {len(all_photos)} (API + scraping Maps)")
    print(f"    Avis           : {len(all_reviews)} uniques (top {len(exported_reviews)} affiches)")
    print()
    print(f"  WORKSPACE ({out_dir}/) :")
    print(f"    CLAUDE.md         -- conducteur build Claude Code")
    print(f"    COMPONENTS.md     -- 7 composants vanilla")
    print(f"    PRODUCT.md        -- contexte metier")
    print(f"    POST_DEPLOY.md    -- workflow apres deploy (demo, vente, domain, FormSubmit)")
    print(f"    reviews_all.json  -- tous les avis bruts (analyse voix client)")
    print(f"    profil.json/.txt  -- donnees brutes scraper")
    print(f"    preview_site.py   -- capture screenshots du site (PHASE 4.5)")
    print(f"    audit_content.py  -- audit anti-hallucination (PHASE 5)")
    print(f"    finalize_site.py  -- remplace __SITE_URL__ + __CLIENT_EMAIL__ apres deploy")
    print()
    print(f"  SITE/ ({site_dir}/) -- deployable :")
    print(f"    (vide, Claude Code y ecrira les .html avec marqueurs __SITE_URL__ et __CLIENT_EMAIL__)")
    print(f"    favicon.svg, sitemap.xml, robots.txt")
    print(f"    reviews.json + reviews_meta.json + reviews_inline.js")
    print(f"    photos/ ({len(all_photos)} .jpg + .webp)")
    print(f"    videos/ (deposer manuellement video_XX.mp4 si besoin)")
    print()
    print(f"  Workflow complet :")
    print(f"    1. [Optionnel] Ajouter videos dans {video_dir}/")
    print(f"    2. Claude Code workspace = {out_dir}/")
    print(f"    3. Prompte Claude : 'Lis CLAUDE.md et construis le site.'")
    print(f"    4. Claude ecrit les .html dans site/ avec marqueurs __SITE_URL__ + __CLIENT_EMAIL__")
    print(f"    5. python preview_site.py    (QC visuel)")
    print(f"    6. python audit_content.py   (QC anti-hallucination)")
    print(f"    7. cd site && vercel --prod  (1er deploy 'blind', recupere l URL Vercel auto)")
    print(f"    8. python finalize_site.py --url <URL_VERCEL> --email <TON_EMAIL>")
    print(f"    9. cd site && vercel --prod  (re-deploy pour la demo client)")
    print(f"    10. Vente + choix du custom domain avec le client")
    print(f"    11. Voir POST_DEPLOY.md pour finalisation (custom domain + email client + FormSubmit)")

    return out_dir


# ─────────────────────────────────────────────────────────────
# POINT D'ENTREE
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Company Scraper — profil complet Google Places")
    parser.add_argument("--key",      help="Cle API Google Places")
    parser.add_argument("--place_id", help="place_id depuis le CSV prospects")
    parser.add_argument("--nom",      help="Nom entreprise")
    parser.add_argument("--ville",    default="", help="Ville (avec --nom)")
    parser.add_argument("--csv",      help="CSV prospects — traitement par lot")
    parser.add_argument("--priorite", help="Filtre priorite : CHAUD | TIEDE | FROID")
    parser.add_argument("--force",     action="store_true")
    args = parser.parse_args()

    api_key = args.key or os.environ.get("GOOGLE_PLACES_API_KEY") or ""
    if not api_key:
        api_key = input("\nCle API Google Places : ").strip()
        if not api_key:
            sys.exit(1)

    OUTPUT_DIR.mkdir(exist_ok=True)

    if args.csv:
        with open(args.csv, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f, delimiter=";"))
        if args.priorite:
            rows = [r for r in rows if r.get("priorite","").upper() == args.priorite.upper()]
        ok = 0
        for row in rows:
            pid = row.get("place_id","").strip()
            if not pid:
                continue
            result = process(pid, api_key, force=args.force)
            if result:
                ok += 1
        print(f"\n  {ok}/{len(rows)} profils generes dans {OUTPUT_DIR}/")

    elif args.place_id:
        process(args.place_id, api_key, force=args.force)

    elif args.nom:
        pid = api_search(args.nom, args.ville, api_key)
        if pid:
            process(pid, api_key, force=args.force)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
