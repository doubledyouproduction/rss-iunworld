#!/usr/bin/env python3
"""
Instagram -> RSS (via Apify)

Holt die Posts aus dem letzten erfolgreichen Lauf deines Apify-Scrapers
und schreibt daraus einen sauberen RSS-Feed nach docs/feed.xml.
Bereits gesehene Posts werden in docs/posts.json gemerkt, damit der Feed
eine Historie behaelt und jeder Post eine stabile ID + Datum bekommt.

Benoetigt nur Python-Standardbibliothek.

Umgebungsvariablen:
  APIFY_TOKEN   (Pflicht)  dein Apify API-Token (als GitHub-Secret hinterlegen)
  APIFY_ACTOR   (optional) Actor-ID, Standard: nH2AHrwxeTRJoN5hX
  MAX_ITEMS     (optional) wie viele Posts der Feed maximal enthaelt, Standard: 50
"""

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

ACTOR = os.environ.get("APIFY_ACTOR", "nH2AHrwxeTRJoN5hX")
TOKEN = os.environ.get("APIFY_TOKEN", "")
MAX_ITEMS = int(os.environ.get("MAX_ITEMS", "50"))
TEST_FILE = os.environ.get("APIFY_JSON_FILE")  # nur fuer lokale Tests

OUT_DIR = Path("docs")
STORE = OUT_DIR / "posts.json"
FEED = OUT_DIR / "feed.xml"
IMG_DIR = OUT_DIR / "img"

# Oeffentliche Adresse deiner GitHub-Pages-Seite (wird in GitHub Actions automatisch erkannt)
_repo = os.environ.get("GITHUB_REPOSITORY", "")  # z. B. "doubledyouproduction/rss-iunworld"
SITE_URL = os.environ.get("SITE_URL") or (
    f"https://{_repo.split('/')[0]}.github.io/{_repo.split('/')[1]}" if "/" in _repo else ""
)


def save_image(key, src):
    """Laedt das Instagram-Bild einmal herunter und legt es unter docs/img/ ab.
    Instagram-Bildlinks laufen ab und werden von Teams oft blockiert, eigene nicht."""
    if not src or not SITE_URL:
        return src
    safe = "".join(c for c in key if c.isalnum() or c in "-_")
    target = IMG_DIR / f"{safe}.jpg"
    if not target.exists():
        try:
            req = urllib.request.Request(src, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            IMG_DIR.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        except Exception as e:  # Bild nicht ladbar -> Original-Link behalten
            print(f"Bild fuer {key} nicht geladen: {e}")
            return src
    return f"{SITE_URL}/img/{target.name}"


def fetch_posts():
    if TEST_FILE:
        return json.loads(Path(TEST_FILE).read_text(encoding="utf-8"))
    if not TOKEN:
        sys.exit("APIFY_TOKEN fehlt.")
    url = (
        f"https://api.apify.com/v2/acts/{ACTOR}/runs/last/dataset/items"
        "?status=SUCCEEDED&clean=true&format=json"
    )
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_iso(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def make_title(caption, fallback):
    if not caption:
        return fallback
    first_line = caption.strip().splitlines()[0].strip()
    return first_line if len(first_line) <= 110 else first_line[:107].rstrip() + "..."


def main():
    OUT_DIR.mkdir(exist_ok=True)
    store = json.loads(STORE.read_text(encoding="utf-8")) if STORE.exists() else {}
    now = datetime.now(timezone.utc)

    added = 0
    for p in fetch_posts():
        url = p.get("url")
        if not url:
            continue
        key = p.get("shortCode") or p.get("id") or url
        if key not in store:
            # pubDate = Zeitpunkt, an dem der Post zum ersten Mal im Feed auftaucht.
            # So erkennt Power Automate ihn sicher als "neu", auch wenn der Scraper
            # ihn erst mit Verzoegerung findet.
            store[key] = {"first_seen": now.isoformat()}
            added += 1
        store[key].update({
            "url": url,
            "caption": p.get("caption") or "",
            "timestamp": p.get("timestamp"),
            "image": save_image(key, p.get("displayUrl")),
            "type": p.get("type"),
            "owner": p.get("ownerUsername"),
            "owner_name": p.get("ownerFullName"),
        })

    # neueste zuerst, Historie begrenzen
    items = sorted(store.items(), key=lambda kv: kv[1]["first_seen"], reverse=True)[:MAX_ITEMS]
    store = dict(items)

    # Bilder von Posts, die nicht mehr im Feed sind, aufraeumen
    if IMG_DIR.exists():
        keep = {Path(v.get("image") or "").name for _, v in items}
        for f in IMG_DIR.glob("*.jpg"):
            if f.name not in keep:
                f.unlink()

    STORE.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")

    owner = next((v.get("owner") for _, v in items if v.get("owner")), "instagram")
    owner_name = next((v.get("owner_name") for _, v in items if v.get("owner_name")), owner)
    profile = f"https://www.instagram.com/{owner}/"

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        "<channel>",
        f"<title>{escape(f'Instagram – {owner_name}')}</title>",
        f"<link>{escape(profile)}</link>",
        f"<description>{escape(f'Neue Instagram-Posts von @{owner}')}</description>",
        "<language>de-de</language>",
        f"<lastBuildDate>{format_datetime(now)}</lastBuildDate>",
    ]
    for key, v in items:
        posted = parse_iso(v.get("timestamp"))
        posted_txt = posted.astimezone().strftime("%d.%m.%Y %H:%M") if posted else ""
        caption_html = escape(v["caption"]).replace("\n", "<br>")
        desc = ""
        if v.get("image"):
            desc += f'<img src="{escape(v["image"])}" alt=""><br>'
        desc += caption_html
        if posted_txt:
            desc += f"<br><br><i>Auf Instagram gepostet: {posted_txt}</i>"

        parts += [
            "<item>",
            f"<title>{escape(make_title(v['caption'], 'Neuer Instagram-Post'))}</title>",
            f"<link>{escape(v['url'])}</link>",
            f'<guid isPermaLink="false">{escape(key)}</guid>',
            f"<pubDate>{format_datetime(parse_iso(v['first_seen']))}</pubDate>",
            f"<description>{escape(desc)}</description>",
        ]
        if v.get("image"):
            parts.append(f'<enclosure url="{escape(v["image"])}" type="image/jpeg" length="0"/>')
        parts.append("</item>")
    parts += ["</channel>", "</rss>", ""]

    FEED.write_text("\n".join(parts), encoding="utf-8")
    print(f"Feed geschrieben: {len(items)} Posts, davon {added} neu.")


if __name__ == "__main__":
    main()
