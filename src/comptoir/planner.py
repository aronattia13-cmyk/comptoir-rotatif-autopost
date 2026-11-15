"""Content rotation engine and publication calendar.

Weekly rhythm (config/settings.json):
  Mon  product post (IG + FB)        Tue  service story (IG) + FB post
  Wed  blog post (IG + FB)           Thu  product story (IG), different product
  Fri  product post, other range (IG + FB)

Anti-repetition, from history: same product, same model (lot 5 vs lot 50),
same article, same service message, same photo, recent layouts.
When nothing new is available a slot is *skipped*, never filled with a repeat.
"""
from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import design
from .blog import Article, usable
from .catalog import Product, publishable
from .history import Entry, History

ROOT = Path(__file__).resolve().parents[2]
SETTINGS = ROOT / "config" / "settings.json"
SERVICES = ROOT / "data" / "content" / "services.json"
BLOG_OVERRIDES = ROOT / "data" / "content" / "blog_overrides.json"

PRODUCT_LAYOUTS = list(design.PRODUCT_VARIANTS)
BLOG_LAYOUTS = list(design.BLOG_VARIANTS)
SERVICE_LAYOUTS = list(design.SERVICE_VARIANTS)


def load_json(p: Path) -> dict:
    return json.loads(p.read_text()) if p.exists() else {}


@dataclass
class PlanItem:
    uid: str
    date: str
    time: str
    slot: str
    family: str
    surface: str
    facebook: bool
    status: str = "planned"          # planned | skipped
    reason: str = ""
    subject: str = ""
    reference: str = ""
    category: str = ""
    layout: str = ""
    photo_url: str = ""
    photo_source: str = ""           # product | article | service | product-fallback
    price: str = ""
    lot_size: int = 0
    title: str = ""
    body: str = ""
    url: str = ""
    payload: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)

    def scheduled_at(self, tz="Europe/Paris") -> datetime:
        h, m = map(int, self.time.split(":"))
        return datetime.fromisoformat(self.date).replace(hour=h, minute=m, tzinfo=ZoneInfo(tz))


def model_key(p: Product) -> str:
    """Same instrument regardless of lot size: 'Turbine…' == 'Turbines…'."""
    words = re.sub(r"[^\w\s:]", " ", p.display_name.lower()).split()
    words = [w[:-1] if w.endswith("s") and len(w) > 3 else w for w in words]
    return f"{p.category}|{' '.join(words)}"


class Planner:
    def __init__(self, products: list[Product], articles: list[Article], history: History,
                 settings: dict | None = None, services: dict | None = None,
                 blog_overrides: dict | None = None, service_photos: list[str] | None = None):
        self.products = publishable(products)
        self.all_products = products
        self.articles = articles
        self.h = history
        self.s = settings or load_json(SETTINGS)
        self.services = services if services is not None else load_json(SERVICES)
        self.overrides = blog_overrides if blog_overrides is not None else load_json(BLOG_OVERRIDES)
        self.cd = self.s["cooldown_days"]
        self.service_photos = service_photos if service_photos is not None else self._find_service_photos()

    # ------------------------------------------------------------------ #
    def _find_service_photos(self) -> list[str]:
        out = []
        for svc in self.services.values():
            folder = ROOT / svc.get("photo_folder", "")
            if folder.is_dir():
                out += sorted(str(p) for p in folder.iterdir()
                              if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
        return out

    def _days(self, today, **match) -> int:
        return self.h.days_since(today, **match)

    def _pick_layout(self, today: date, pool: list[str]) -> str:
        recent = [e.layout for e in self.h.recent(self.s.get("avoid_last_layouts", 2))]
        candidates = [l for l in pool if l not in recent] or pool
        return max(candidates, key=lambda l: (self._days(today, layout=l), -pool.index(l)))

    def _pick_weighted(self, today: date, weights: dict[str, float]) -> str:
        """Largest-deficit rotation: over the recent product visuals, pick the
        variant furthest below its target share (70/20/10 converges exactly),
        never the same rare variant twice in a row."""
        if isinstance(weights, list):
            weights = {v: 1 / len(weights) for v in weights}
        recent = [e.layout for e in self.h.recent(40, family="product") if e.layout in weights]
        n = len(recent) + 1
        last = recent[-1] if recent else None

        def deficit(v):
            return weights[v] * n - recent.count(v)
        order = sorted(weights, key=lambda v: (-deficit(v), -weights[v]))
        for v in order:
            if v == last and weights[v] < 0.5 and len(order) > 1:
                continue
            return v
        return order[0]

    # ------------------------------------------------------------------ #
    def pick_product(self, today: date, week_categories: list[str], surface: str, hard_other_range: bool):
        cd = self.cd
        cands = []
        for p in self.products:
            if self._days(today, reference=p.handle) < cd["same_product"]:
                continue
            if self._days(today, subject=model_key(p)) < cd["same_model"]:
                continue
            if hard_other_range and p.category in week_categories:
                continue
            cands.append(p)
        if not cands:
            return None
        cands.sort(key=lambda p: (
            p.category in week_categories,                                   # new range this week first
            -min(self._days(today, category=p.category, family="product"), 999),
            -min(self._days(today, subject=model_key(p)), 999),
            -min(self._days(today, reference=p.handle), 999),
            p.handle,
        ))
        return cands[0]

    def article_settings(self, a: Article) -> dict:
        o = self.overrides.get(a.handle, {})
        title = o.get("short_title") or (a.title.split(":")[0].strip()
                                         if ":" in a.title and len(a.title) > 70 else a.title)
        return {"short_title": title, "enabled": o.get("enabled", True),
                "use_article_image": o.get("use_article_image", True),
                "photo_category": o.get("photo_category", "turbine")}

    def pick_article(self, today: date):
        cands = [a for a in usable(self.articles)
                 if self.article_settings(a)["enabled"]
                 and self._days(today, reference=a.url) >= self.cd["same_article"]]
        if not cands:
            return None
        cands.sort(key=lambda a: (-min(self._days(today, reference=a.url), 999), a.published), reverse=False)
        # never used first, then newest article
        never = [a for a in cands if self._days(today, reference=a.url) >= 999]
        pool = never or cands
        return sorted(pool, key=lambda a: a.published, reverse=True)[0]

    def product_photo(self, today: date, category: str) -> str | None:
        pool = [p.image_url for p in self.products if p.category == category and p.image_url]
        pool = list(dict.fromkeys(pool))
        if not pool:
            return None
        return max(pool, key=lambda u: (self._days(today, photo=u), -pool.index(u)))

    def pick_service(self, today: date):
        for sid, svc in self.services.items():
            if not svc.get("enabled"):
                continue
            msgs = [m for m in svc["messages"]
                    if self._days(today, subject=m["id"]) >= self.cd["same_service_message"]]
            if not msgs:
                continue
            msg = max(msgs, key=lambda m: (self._days(today, subject=m["id"]), -svc["messages"].index(m)))
            if self.service_photos:          # real engraving photos only
                photos = [u for u in self.service_photos
                          if self._days(today, photo=u) >= self.cd["same_service_photo"]] or self.service_photos
                photo = max(photos, key=lambda u: self._days(today, photo=u))
                source = "service"
            else:                            # no real photo: typographic visual
                photo, source = None, "typographic"
            return sid, svc, msg, photo, source
        return None

    # ------------------------------------------------------------------ #
    def plan(self, start: date, days: int, commit_to_history: bool = True) -> list[PlanItem]:
        """Plan [start, start+days). Planned items are written to history
        (status 'planned') so later choices see them — use a History copy
        for simulations."""
        items: list[PlanItem] = []
        slots = {s["weekday"]: s for s in self.s["weekly_slots"]}
        week_cats: dict[int, list[str]] = {}
        for i in range(days):
            day = start + timedelta(days=i)
            s = slots.get(day.weekday())
            if not s:
                continue
            uid = f"{day.isoformat()}-{s['slot']}"
            if self.h.has(uid):
                continue                       # already planned / published
            item = PlanItem(uid=uid, date=day.isoformat(), time=s["time"], slot=s["slot"],
                            family=s["family"], surface=s["surface"], facebook=s.get("facebook", False))
            wk = day.isocalendar()[1]
            cats = week_cats.setdefault(wk, [])
            if s["family"] == "product":
                p = self.pick_product(day, cats, s["surface"], hard_other_range=False)   # other range preferred
                if not p:
                    item.status, item.reason = "skipped", "aucun produit non publié récemment"
                else:
                    cats.append(p.category)
                    item.subject, item.reference, item.category = model_key(p), p.handle, p.category
                    item.price, item.lot_size, item.title, item.url = str(p.price), p.lot_size, p.display_name, p.url
                    item.photo_url, item.photo_source = p.image_url, "product"
                    item.layout = self._pick_weighted(day, self.s.get("product_variants") or
                                                      {v: 1 / len(PRODUCT_LAYOUTS) for v in PRODUCT_LAYOUTS})
            elif s["family"] == "blog":
                a = self.pick_article(day)
                p = None if a else (self.pick_product(day, cats, s["surface"], hard_other_range=True)
                                    or self.pick_product(day, cats, s["surface"], hard_other_range=False))
                if not a and p:
                    # No unused article: replace by a verified product from another range.
                    cats.append(p.category)
                    item.family = "product"
                    item.reason = "remplacement : aucun nouvel article de blog disponible"
                    item.subject, item.reference, item.category = model_key(p), p.handle, p.category
                    item.price, item.lot_size, item.title, item.url = str(p.price), p.lot_size, p.display_name, p.url
                    item.photo_url, item.photo_source = p.image_url, "product"
                    item.layout = self._pick_weighted(day, self.s.get("product_variants") or
                                                      {v: 1 / len(PRODUCT_LAYOUTS) for v in PRODUCT_LAYOUTS})
                elif not a:
                    item.status, item.reason = "skipped", "aucun nouvel article ni produit disponible"
                else:
                    st = self.article_settings(a)
                    item.subject = item.reference = a.url
                    item.title, item.url, item.category = st["short_title"], a.url, "blog"
                    if st["use_article_image"] and a.image_url:
                        item.photo_url, item.photo_source = a.image_url, "article"
                    else:
                        item.photo_url = self.product_photo(day, st["photo_category"]) or ""
                        item.photo_source = "product"
                    item.layout = self._pick_layout(day, BLOG_LAYOUTS)
            elif s["family"] == "service":
                r = self.pick_service(day)
                if not r:
                    item.status, item.reason = "skipped", "aucun message de service disponible"
                else:
                    sid, svc, msg, photo, source = r
                    item.subject, item.reference, item.category = msg["id"], sid, "service"
                    item.title, item.body = msg["title"], msg["body"]
                    item.photo_url, item.photo_source = photo or "", source
                    item.layout = self._pick_layout(day, SERVICE_LAYOUTS)
            items.append(item)
            if item.status == "planned":
                self.h.add(Entry(uid=uid, date=item.date, slot=item.slot, family=item.family,
                                 surface=item.surface, subject=item.subject, reference=item.reference,
                                 layout=item.layout, photo=item.photo_url, category=item.category,
                                 status="planned", price=item.price))
        return items


def simulate(products, articles, history: History, start: date, weeks: int = 4, **kw) -> list[PlanItem]:
    """Dry planning on a copy of the history (nothing saved)."""
    h = History(copy.deepcopy(history.entries), history.path)
    return Planner(products, articles, h, **kw).plan(start, weeks * 7)


def mix(items: list[PlanItem]) -> dict[str, float]:
    done = [i for i in items if i.status == "planned"]
    n = len(done) or 1
    return {f: round(100 * sum(i.family == f for i in done) / n) for f in ("product", "blog", "service")}
