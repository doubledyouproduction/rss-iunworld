#!/usr/bin/env python3
"""
Social Media -> RSS (via Apify)

Holt die Posts aus dem letzten erfolgreichen Lauf deiner Apify-Scraper und
schreibt pro Plattform einen sauberen RSS-Feed:

  Instagram -> docs/feed.xml       (gemerkte Posts: docs/posts.json)
  LinkedIn  -> docs/linkedin.xml   (gemerkte Posts: docs/linkedin_posts.json)

Bilder werden einmal heruntergeladen und unter docs/img/ abgelegt, weil die
Original-Bildlinks von Instagram/LinkedIn ablaufen und Teams sie oft blockiert.

Benoetigt nur die Python-Standardbibliothek.

Umgebungsvariablen:
  APIFY_TOKEN            (Pflicht)  dein Apify API-Token (GitHub-Secret)
  APIFY_INSTAGRAM_ACTOR  (optional) Actor-ID Instagram, Standard: nH2AHrwxeTRJoN5hX
  APIFY_LINKEDIN_ACTOR   (optional) Actor-ID LinkedIn; leer = LinkedIn wird uebersprungen
  MAX_ITEMS              (optional) max. Posts pro Feed, Standard: 50
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

TOKEN = os.environ.get("APIFY_TOKEN", "")
MAX_ITEMS = int(os.environ.get("MAX_ITEMS", "50"))

OUT_DIR = Path("docs")
IMG_DIR = OUT_DIR / "img"

# Oeffentliche Adresse deiner GitHub-Pages-Seite (in GitHub Actions automatisch erkannt)
_repo = os.environ.get("GITHUB_REPOSITORY", "")  # z. B. "doubledyouproduction/rss-iunworld"
SITE_URL = os.environ.get("SITE_URL") or (
    f"https://{_repo.split('/')[0]}.github.io/{_repo.split('/')[1]}" if "/" in _repo else ""
)


# ---------------------------------------------------------------------------
# Plattform-spezifisch: Rohdaten des Scrapers -> einheitliches Format
# ---------------------------------------------------------------------------

def normalize_instagram(p):
    if not p.get("url"):
        return None
    user = p.get("ownerUsername") or ""
    return {
        "key": p.get("shortCode") or p.get("id") or p["url"],
        "url": p["url"],
        "text": p.get("caption") or "",
        "timestamp": p.get("timestamp"),
        "image_src": p.get("displayUrl"),
        "owner": p.get("ownerFullName") or user,
        "handle": f"@{user}" if user else "",
        "profile": f"https://www.instagram.com/{user}/" if user else "https://www.instagram.com/",
        "note": "",
    }


def _first(*values):
    return next((v for v in values if v), None)


def normalize_linkedin(p):
    url = _first(p.get("linkedinUrl"), p.get("shareLinkedinUrl"))
    if not url:
        return None
    author = p.get("author") or {}
    reposted = p.get("repostedBy") or {}
    doc_pages = ((p.get("document") or {}).get("coverPages") or [{}])[0].get("imageUrls") or []
    image = _first(
        ((p.get("postImages") or [{}])[0]).get("url"),
        (p.get("postVideo") or {}).get("thumbnailUrl"),
        ((p.get("article") or {}).get("image") or {}).get("url"),
        doc_pages[0] if doc_pages else None,
    )
    owner_name = reposted.get("name") or author.get("name") or "LinkedIn"
    profile = (reposted.get("linkedinUrl") or author.get("linkedinUrl") or "https://www.linkedin.com/")
    profile = profile.split("?")[0]
    note = f"Geteilt von {owner_name}, ursprünglich von {author.get('name')}" if reposted and author.get("name") else ""
    article = p.get("article") or {}
    text = p.get("content") or ""
    if article.get("title") and article["title"] not in text:
        text = f"{text}\n\n📰 {article['title']}".strip()
    return {
        "key": str(_first(p.get("entityId"), p.get("id"), url)),
        "url": url,
        "text": text,
        "timestamp": (p.get("postedAt") or {}).get("date"),
        "image_src": image,
        "owner": owner_name,
        "handle": owner_name,
        "profile": profile,
        "note": note,
    }


SOURCES = [
    {
        "name": "Instagram",
        "actor": os.environ.get("APIFY_INSTAGRAM_ACTOR", "nH2AHrwxeTRJoN5hX"),
        "store": OUT_DIR / "posts.json",
        "feed": OUT_DIR / "feed.xml",
        "img_prefix": "",          # leer = kompatibel mit bisherigen Bildern
        "normalize": normalize_instagram,
        "fallback_title": "Neuer Instagram-Post",
        "test_env": "APIFY_JSON_FILE",
    },
    {
        "name": "LinkedIn",
        "actor": os.environ.get("APIFY_LINKEDIN_ACTOR", ""),
        "store": OUT_DIR / "linkedin_posts.json",
        "feed": OUT_DIR / "linkedin.xml",
        "img_prefix": "li-",
        "normalize": normalize_linkedin,
        "fallback_title": "Neuer LinkedIn-Post",
        "test_env": "LINKEDIN_JSON_FILE",
    },
]


# ---------------------------------------------------------------------------
# Gemeinsame Logik
# ---------------------------------------------------------------------------

def fetch_posts(source):
    test_file = os.environ.get(source["test_env"])  # nur fuer lokale Tests
    if test_file:
        return json.loads(Path(test_file).read_text(encoding="utf-8"))
    if not TOKEN:
        raise RuntimeError("APIFY_TOKEN fehlt.")
    url = (
        f"https://api.apify.com/v2/acts/{source['actor']}/runs/last/dataset/items"
        "?status=SUCCEEDED&clean=true&format=json"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def save_image(name, src):
    """Laedt das Bild einmal herunter und legt es unter docs/img/ ab."""
    if not src or not SITE_URL or src.startswith(SITE_URL):
        return src
    safe = "".join(c for c in name if c.isalnum() or c in "-_")
    target = IMG_DIR / f"{safe}.jpg"
    if not target.exists():
        try:
            req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            IMG_DIR.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except Exception as e:  # Bild nicht ladbar -> Original-Link behalten
            print(f"  Bild {name} nicht geladen: {e}")
            return src
    return f"{SITE_URL}/img/{target.name}"


def parse_iso(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def make_title(text, fallback):
    if not text or not text.strip():
        return fallback
    first_line = text.strip().splitlines()[0].strip()
    return first_line if len(first_line) <= 110 else first_line[:107].rstrip() + "..."


# Kurznamen der Hochschulen: Suchbegriff (klein geschrieben) -> Anzeige in Teams
SHORT_NAMES = [
    ("seeburg", "Seeburg"),
    ("dhgs", "DHGS"),
    ("health-and-sport", "DHGS"),
    ("health and sport", "DHGS"),
    ("gesundheit und sport", "DHGS"),
    ("ham_hochschule", "HAM"),
    ("hochschule - ham", "HAM"),
    ("angewandtes management", "HAM"),
    ("angewandtes-management", "HAM"),
]


def short_name(v):
    haystack = " ".join(str(v.get(k) or "") for k in ("owner", "handle", "profile")).lower()
    for needle, label in SHORT_NAMES:
        if needle in haystack:
            return label
    return v.get("owner") or ""


def header(v, platform):
    """z. B. 'Instagram | HAM' oder 'LinkedIn | Seeburg'"""
    name = short_name(v)
    return f"{platform} | {name}" if name else platform


def build_source(source, now):
    store = json.loads(source["store"].read_text(encoding="utf-8")) if source["store"].exists() else {}

    # Eintraege aus der ersten Skript-Version (nur Instagram) ins neue Format bringen
    for v in store.values():
        if "caption" in v:
            v["text"] = v.pop("caption")
        if "owner_name" in v:
            user = v.get("owner") or ""
            v["owner"] = v.pop("owner_name") or user
            v["handle"] = f"@{user}"
            v["profile"] = f"https://www.instagram.com/{user}/"
            v.setdefault("note", "")
        v.pop("type", None)

    added = 0
    for raw in fetch_posts(source):
        post = source["normalize"](raw)
        if not post:
            continue
        key = post["key"]
        if key not in store:
            # pubDate = Zeitpunkt, an dem der Post zum ersten Mal im Feed auftaucht.
            # So erkennt Power Automate ihn sicher als "neu".
            store[key] = {"first_seen": now.isoformat()}
            added += 1
        store[key].update({
            "url": post["url"],
            "text": post["text"],
            "timestamp": post["timestamp"],
            "image": save_image(source["img_prefix"] + key, post["image_src"]),
            "owner": post["owner"],
            "handle": post["handle"],
            "profile": post["profile"],
            "note": post["note"],
        })

    items = sorted(store.items(), key=lambda kv: kv[1]["first_seen"], reverse=True)[:MAX_ITEMS]
    source["store"].write_text(json.dumps(dict(items), ensure_ascii=False, indent=2), encoding="utf-8")

    pname = source["name"]
    owners = list(dict.fromkeys(v.get("owner") for _, v in items if v.get("owner")))
    handles = list(dict.fromkeys(v.get("handle") for _, v in items if v.get("handle")))
    profile = next((v.get("profile") for _, v in items if v.get("profile")), "")
    if len(owners) > 1:  # mehrere Accounts in einem Feed
        channel_title = f"{pname} – {', '.join(owners)}"
        channel_desc = f"Neue {pname}-Posts von {', '.join(handles)}"
        profile = "https://www.instagram.com/" if pname == "Instagram" else "https://www.linkedin.com/"
    else:
        owner = owners[0] if owners else pname
        channel_title = f"{pname} – {owner}"
        channel_desc = f"Neue {pname}-Posts von {handles[0] if handles else owner}"
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "<channel>",
        f"<title>{escape(channel_title)}</title>",
        f"<link>{escape(profile)}</link>",
        f"<description>{escape(channel_desc)}</description>",
        "<language>de-de</language>",
        f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>",
    ]
    for key, v in items:
        posted = parse_iso(v.get("timestamp"))
        posted_txt = posted.astimezone().strftime("%d.%m.%Y %H:%M") if posted else ""
        # Herkunft als erste Zeile, damit sie in Teams ganz oben steht
        desc = f"<b>{escape(header(v, pname))}</b><br><br>"
        if v.get("image"):
            desc += f'<img src="{escape(v["image"])}" alt=""><br>'
        desc += escape(v["text"]).replace("\n", "<br>")
        if v.get("note"):
            desc += f"<br><br><i>{escape(v['note'])}</i>"
        if posted_txt:
            desc += f"<br><br><i>Auf {source['name']} gepostet: {posted_txt}</i>"

        parts += [
            "<item>",
            # Account-Name vorne, damit in Teams sofort klar ist, von wem der Post kommt
            f"<title>{escape('[' + header(v, pname) + '] ' + make_title(v['text'], source['fallback_title']))}</title>",
            f"<link>{escape(v['url'])}</link>",
            f'<guid isPermaLink="false">{escape(key)}</guid>',
            f"<pubDate>{format_datetime(parse_iso(v['first_seen']))}</pubDate>",
            f"<description>{escape(desc)}</description>",
        ]
        if v.get("image"):
            parts.append(f'<enclosure url="{escape(v["image"])}" type="image/jpeg" length="0"/>')
        parts.append("</item>")
    parts += ["</channel>", "</rss>", ""]

    source["feed"].write_text("\n".join(parts), encoding="utf-8")
    print(f"{source['name']}: {len(items)} Posts im Feed, davon {added} neu.")
    return {Path(v.get("image") or "").name for _, v in items}


def main():
    OUT_DIR.mkdir(exist_ok=True)
    now = datetime.now(timezone.utc)
    keep_images, failed = set(), []

    for source in SOURCES:
        if not source["actor"] and not os.environ.get(source["test_env"]):
            print(f"{source['name']}: keine Actor-ID eingetragen, uebersprungen.")
            if source["store"].exists():  # Bilder bestehender Feeds nicht loeschen
                keep_images |= {Path(v.get("image") or "").name
                                for v in json.loads(source["store"].read_text(encoding="utf-8")).values()}
            continue
        try:
            keep_images |= build_source(source, now)
        except Exception as e:
            # Ein Fehler bei einer Plattform soll die andere nicht blockieren
            print(f"FEHLER bei {source['name']}: {e}")
            failed.append(source["name"])
            if source["store"].exists():
                keep_images |= {Path(v.get("image") or "").name
                                for v in json.loads(source["store"].read_text(encoding="utf-8")).values()}

    # Bilder von Posts, die in keinem Feed mehr vorkommen, aufraeumen
    if IMG_DIR.exists():
        for f in IMG_DIR.glob("*.jpg"):
            if f.name not in keep_images:
                f.unlink()

    if failed:
        sys.exit(f"Fehlgeschlagen: {', '.join(failed)} (die anderen Feeds wurden trotzdem aktualisiert)")


if __name__ == "__main__":
    main()
