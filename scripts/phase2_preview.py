"""Phase 2 — 4-week simulated calendar (Direction A) (TEST mode, nothing published).

Usage: python scripts/phase2_preview.py [--start 2026-11-16] [--weeks 4]
Output: site/preview/ (calendar page + images), site/demo2/ (9 visuals),
        data/plan_preview.json, data/articles.json
"""
from __future__ import annotations

import argparse
import html
import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image  # noqa: E402

from comptoir import blog, build, catalog, planner  # noqa: E402
from comptoir.history import History  # noqa: E402

FR_DAYS = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]
FAMILY_FR = {"product": "Produit", "blog": "Blog", "service": "Service"}


def next_monday(d: date) -> date:
    return d + timedelta(days=(7 - d.weekday()) % 7 or 7)


def thumb(src: Path, dst: Path, w=480):
    im = Image.open(src)
    im.thumbnail((w, w * 2))
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, quality=85)


def preview_html(items, mix, start, notes) -> str:
    rows = []
    for it in items:
        d = date.fromisoformat(it.date)
        head = f"{FR_DAYS[d.weekday()]} {d.strftime('%d/%m')} · {it.time}"
        if it.status != "planned":
            rows.append(f'<div class="card skip"><div class="meta">{head}</div>'
                        f'<div class="fam">{FAMILY_FR[it.family]} · {it.surface}</div>'
                        f'<p class="reason">Pas de publication : {html.escape(it.reason)}</p></div>')
            continue
        img = it.payload["thumb"]
        cap = html.escape(it.payload.get("caption_instagram") or "(Story : pas de légende)")
        fb = html.escape(it.payload.get("caption_facebook") or "")
        extra = f"<details><summary>Facebook</summary><pre>{fb}</pre></details>" if fb else ""
        rows.append(f'''<div class="card {it.family}">
<div class="meta">{head}</div>
<div class="fam">{FAMILY_FR[it.family]} · {"Story" if it.surface == "story" else "Post"}{" + Facebook" if it.facebook else ""} · <code>{it.layout}</code></div>
<img src="{img}" alt="">
<div class="title">{html.escape(it.title)}</div>
<details><summary>Légende Instagram</summary><pre>{cap}</pre></details>{extra}
</div>''')
    weeks = []
    for w in range(0, len(rows), 5):
        weeks.append(f'<h2>Semaine du {(start + timedelta(weeks=w // 5)).strftime("%d/%m/%Y")}</h2>'
                     f'<div class="grid">{"".join(rows[w:w + 5])}</div>')
    notes_html = "".join(f"<li>{html.escape(n)}</li>" for n in notes)
    return f'''<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Calendrier — Le Comptoir du Rotatif</title>
<style>
:root{{--ink:#141e2c;--muted:#707884;--line:#dfe2e7;--bg:#f6f6f4}}
*{{box-sizing:border-box}}body{{margin:0;font:15px/1.45 Inter,system-ui,sans-serif;color:var(--ink);background:var(--bg)}}
header{{padding:32px 28px 8px;max-width:1500px;margin:auto}}h1{{font-weight:500;font-size:26px;margin:0 0 6px}}
.sub{{color:var(--muted)}}.mix{{display:flex;gap:10px;margin:16px 0;flex-wrap:wrap}}
.pill{{background:#fff;border:1px solid var(--line);padding:6px 12px;border-radius:99px;font-size:13px}}
main{{max-width:1500px;margin:auto;padding:0 28px 60px}}h2{{font-weight:500;font-size:17px;margin:34px 0 12px;border-top:1px solid var(--line);padding-top:18px}}
.grid{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px}}
@media(max-width:1100px){{.grid{{grid-template-columns:repeat(2,1fr)}}}}@media(max-width:600px){{.grid{{grid-template-columns:1fr}}}}
.card{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px;display:flex;flex-direction:column;gap:6px}}
.card img{{width:100%;border-radius:4px;border:1px solid var(--line)}}
.meta{{font-weight:600;font-size:13px}}.fam{{font-size:12px;color:var(--muted)}}.title{{font-size:13px}}
.product{{border-top:3px solid #3a5a80}}.blog{{border-top:3px solid #8a7a5a}}.service{{border-top:3px solid #4f7a6a}}
.skip{{background:transparent;border-style:dashed}}.reason{{color:var(--muted);font-size:13px;margin:4px 0}}
details summary{{cursor:pointer;font-size:12px;color:var(--muted)}}pre{{white-space:pre-wrap;font:12px/1.4 inherit;background:var(--bg);padding:8px;border-radius:6px}}
code{{font-size:11px}}ul{{color:var(--muted);font-size:13px}}
</style></head><body><header><h1>Calendrier prévisionnel — 4 semaines</h1>
<div class="sub">Mode TEST · aucune publication réelle · fuseau Europe/Paris · généré automatiquement</div>
<div class="mix"><span class="pill">Produits {mix["product"]} %</span><span class="pill">Blog {mix["blog"]} %</span>
<span class="pill">Services {mix["service"]} %</span><span class="pill">{sum(i.status == "planned" for i in items)} publications / {len(items)} créneaux</span></div>
<ul>{notes_html}</ul></header><main>{"".join(weeks)}</main></body></html>'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", type=date.fromisoformat, default=next_monday(date.today()))
    ap.add_argument("--weeks", type=int, default=4)
    args = ap.parse_args()

    products = catalog.load_catalog()
    articles = blog.fetch_articles()
    (ROOT / "data" / "articles.json").write_text(
        json.dumps([a.to_dict() for a in articles], ensure_ascii=False, indent=1))

    history = History.load()
    items = planner.simulate(products, articles, history, args.start, args.weeks)

    out = ROOT / "site" / "preview"
    shutil.rmtree(out, ignore_errors=True)
    for it in items:
        build.build_item(it, products, articles, out_dir=out / "media")
        if it.status == "planned":
            t = out / "thumbs" / f"{it.uid}.jpg"
            thumb(ROOT / it.payload["image"], t)
            it.payload["thumb"] = f"thumbs/{it.uid}.jpg"
    mix = planner.mix(items)

    p = planner.Planner(products, articles, History([]))
    disabled = [a for a in articles if not p.article_settings(a)["enabled"]]
    notes = [f"{len(catalog.publishable(products))} produits publiables, "
             f"{len(blog.usable(articles))} articles de blog réels ({len(disabled)} désactivé en attente de relecture)."]
    if not p.service_photos:
        notes.append("Gravure : aucune photo réelle d'instrument gravé fournie ; visuels typographiques en attendant.")
    skipped = [i for i in items if i.status != "planned"]
    if skipped:
        notes.append(f"{len(skipped)} créneau(x) volontairement vide(s) faute de contenu nouveau (pas de doublon).")
    (out / "index.html").write_text(preview_html(items, mix, args.start, notes))
    (ROOT / "data" / "plan_preview.json").write_text(
        json.dumps({"start": args.start.isoformat(), "mix": mix, "items": [i.to_dict() for i in items]},
                   ensure_ascii=False, indent=1))
    print(f"[plan] {sum(i.status == 'planned' for i in items)}/{len(items)} créneaux, mix {mix}")



def sheet(paths, out, cols=3, h=640):
    ims = [Image.open(p) for p in paths]
    ims = [im.resize((int(im.width * h / im.height), h)) for im in ims]
    cw = max(im.width for im in ims)
    gap = 24
    rows = (len(ims) + cols - 1) // cols
    s = Image.new("RGB", (cols * cw + (cols + 1) * gap, rows * h + (rows + 1) * gap), (225, 228, 232))
    for i, im in enumerate(ims):
        x = gap + (i % cols) * (cw + gap) + (cw - im.width) // 2
        s.paste(im, (x, gap + (i // cols) * (h + gap)))
    s.save(out, quality=88)


if __name__ == "__main__":
    main()
