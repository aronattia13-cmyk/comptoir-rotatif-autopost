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
TIPS = ROOT / "data" / "content" / "tips.json"

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


def product_family(p: Product, families: dict) -> str | None:
    """Rotation family of a product (settings 'product_families': name -> keywords, all required)."""
    k = model_key(p)
    for name, words in families.items():
        if all(w in k for w in words):
            return name
    return None


class Planner:
    def __init__(self, products: list[Product], articles: list[Article], history: History,
                 settings: dict | None = None, services: dict | None = None,
                 blog_overrides: dict | None = None, service_photos: list[str] | None = None,
                 tips: list[dict] | None = None):
        self.products = publishable(products)
        self.all_products = products
        self.articles = articles
        self.h = history
        self.s = settings or load_json(SETTINGS)
        self.services = services if services is not None else load_json(SERVICES)
        self.overrides = blog_overrides if blog_overrides is not None else load_json(BLOG_OVERRIDES)
        self.cd = self.s["cooldown_days"]
        self.service_photos = service_photos if service_photos is not None else self._find_service_photos()
        self.tips = load_json(TIPS).get("tips", []) if tips is None else tips
        self.families = self.s.get("product_families") or {}

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


    # ---------------- rythme 7 j/7 : rotation par familles ---------------- #
    def _family_days(self, fam: str, today: date) -> list[date]:
        out = []
        for e in self.h.entries:
            if e.family != "product" or e.status in ("missed", "skipped"):
                continue
            p = next((x for x in self.all_products if x.handle == e.reference), None)
            if p and product_family(p, self.families) == fam:
                out.append(date.fromisoformat(e.date))
        return out

    def visual_used(self, handle: str, surface: str, layout: str, today: date | None = None) -> bool:
        """Même produit + lot + support + visuel déjà utilisé dans les cooldown_days.same_visual derniers jours
        (doublon exact refusé ; retour possible seulement après ce délai, pour durer des années)."""
        days = self.cd.get("same_visual", 10_000)
        return any(e.family == "product" and e.reference == handle and e.surface == surface
                   and e.layout == layout and e.status not in ("missed", "skipped")
                   and (today is None or (today - date.fromisoformat(e.date)).days < days) for e in self.h.entries)

    def pick_product_rotation(self, today: date, surface: str):
        """Famille la moins récente, jamais la même que la veille ou le jour même ;
        dans la famille, le produit le moins récent (alterne lots 5/50, LED/sans LED) ;
        jamais deux fois le même produit + lot + support + visuel."""
        weights = self.s.get("product_variants") or {v: 1 / len(PRODUCT_LAYOUTS) for v in PRODUCT_LAYOUTS}
        order = sorted(weights, key=lambda v: -weights[v])
        blocked = set()
        for fam in self.families:
            if any(d in (today, today - timedelta(days=1)) for d in self._family_days(fam, today)):
                blocked.add(fam)
        def last_surface(f):
            es = [e for e in self.h.entries if e.family == "product" and e.status not in ("missed", "skipped")
                  and e.reference in {p.handle for p in self.all_products if product_family(p, self.families) == f}]
            return max(es, key=lambda e: e.date).surface if es else None

        def key(f):
            last = max(self._family_days(f, today), default=date(2000, 1, 1))
            # Alterner posts et stories pour chaque famille (Facebook voit toutes les familles) :
            # une famille vue en dernier sur ce même support passe après, à ancienneté proche.
            if last_surface(f) == surface:
                last += timedelta(days=self.s.get("same_surface_penalty_days", 4))
            return (last, list(self.families).index(f))
        fams = sorted([f for f in self.families if f not in blocked], key=key)
        # Rotation décalée d'un cran chaque semaine : chaque famille passe par tous les créneaux
        # produits (posts et stories, jours différents). Préférence seulement : si la famille
        # prévue est bloquée (veille / même jour) ou sans visuel inédit, la plus ancienne prend le relais.
        slots = sorted([(x["weekday"], x["time"]) for x in self.s["weekly_slots"] if x["family"] == "product"])
        me = next((k for k, (wd, _) in enumerate(slots) if wd == today.weekday()
                   and [x for x in self.s["weekly_slots"] if x["weekday"] == wd and x["family"] == "product"
                        and x["surface"] == surface]), None)
        if me is not None and self.families:
            week = (today - date(2024, 1, 1)).days // 7          # 2024-01-01 est un lundi
            pref = list(self.families)[(me + week) % len(self.families)]
            if pref in fams:
                fams.remove(pref)
                fams.insert(0, pref)
        for fam in fams:
            cands = []
            for p in self.products:
                if product_family(p, self.families) != fam:
                    continue
                free = [l for l in order if not self.visual_used(p.handle, surface, l, today)]
                if free:
                    cands.append((self.h.last_date(reference=p.handle) or date(2000, 1, 1), p.handle, p, free[0]))
            if cands:
                _, _, p, layout = min(cands, key=lambda c: (c[0], c[1]))
                return p, layout
        return None

    def pick_tip(self, today: date):
        cd = self.cd.get("same_tip", 60)
        ok = [t for t in self.tips if t.get("validated") and t.get("enabled", True)
              and self._days(today, subject=t["id"]) >= cd]
        if not ok:
            return None
        return max(ok, key=lambda t: (self._days(today, subject=t["id"]), -self.tips.index(t)))

    def _fill_product(self, item: PlanItem, p: Product, layout: str):
        item.family = "product"
        item.subject, item.reference, item.category = model_key(p), p.handle, p.category
        item.price, item.lot_size, item.title, item.url = str(p.price), p.lot_size, p.display_name, p.url
        item.photo_url, item.photo_source, item.layout = p.image_url, "product", layout

    def _fill_tip(self, item: PlanItem, t: dict, day: date):
        item.family = "tip"
        item.subject, item.reference, item.category = t["id"], "conseils", "tip"
        item.title, item.body = t["title"], t["body"]
        item.photo_url, item.photo_source = "", "typographic"
        item.layout = self._pick_layout(day, SERVICE_LAYOUTS)

    def _fill_service(self, item: PlanItem, day: date) -> bool:
        r = self.pick_service(day)
        if not r:
            return False
        sid, svc, msg, photo, source = r
        item.family = "service"
        item.subject, item.reference, item.category = msg["id"], sid, "service"
        item.title, item.body = msg["title"], msg["body"]
        item.photo_url, item.photo_source = photo or "", source
        item.layout = self._pick_layout(day, SERVICE_LAYOUTS)
        return True

    def _fill_blog(self, item: PlanItem, day: date) -> bool:
        a = self.pick_article(day)
        if not a:
            return False
        st = self.article_settings(a)
        item.family = "blog"
        item.subject = item.reference = a.url
        item.title, item.url, item.category = st["short_title"], a.url, "blog"
        if st["use_article_image"] and a.image_url:
            item.photo_url, item.photo_source = a.image_url, "article"
        else:
            item.photo_url = self.product_photo(day, st["photo_category"]) or ""
            item.photo_source = "product"
        item.layout = self._pick_layout(day, BLOG_LAYOUTS)
        return True

    FALLBACKS = {"product": ["product", "tip", "blog", "service"], "blog": ["blog", "tip", "service"],
                 "tip": ["tip", "blog", "service"], "service": ["service", "tip", "blog"]}

    def fill_rotation(self, item: PlanItem, day: date):
        """Remplit un créneau du rythme 7 j/7, avec repli sans répétition ; sinon créneau vide (signalé)."""
        for fam in self.FALLBACKS.get(item.family, [item.family]):
            if fam == "product":
                r = self.pick_product_rotation(day, item.surface)
                if r:
                    return self._fill_product(item, *r)
            elif fam == "tip":
                t = self.pick_tip(day)
                if t:
                    return self._fill_tip(item, t, day)
            elif fam == "blog" and self._fill_blog(item, day):
                return
            elif fam == "service" and self._fill_service(item, day):
                return
        item.status, item.reason = "skipped", "aucun contenu inédit et validé disponible (conseils à valider ?)"

    # ------------------------------------------------------------------ #
    def plan(self, start: date, days: int, commit_to_history: bool = True) -> list[PlanItem]:
        """Plan [start, start+days). Planned items are written to history
        (status 'planned') so later choices see them — use a History copy
        for simulations."""
        items: list[PlanItem] = []
        slots: dict[int, list[dict]] = {}
        for sl in self.s["weekly_slots"]:
            slots.setdefault(sl["weekday"], []).append(sl)
        rotation = bool(self.families)
        week_cats: dict[int, list[str]] = {}
        todo = [(start + timedelta(days=i), s) for i in range(days)
                for s in sorted(slots.get((start + timedelta(days=i)).weekday(), []), key=lambda x: x["time"])]
        for day, s in todo:
            uid = f"{day.isoformat()}-{s['slot']}"
            if self.h.has(uid):
                continue                       # already planned / published
            item = PlanItem(uid=uid, date=day.isoformat(), time=s["time"], slot=s["slot"],
                            family=s["family"], surface=s["surface"], facebook=s.get("facebook", False))
            wk = day.isocalendar()[1]
            cats = week_cats.setdefault(wk, [])
            if rotation:
                self.fill_rotation(item, day)
            elif s["family"] == "product":
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
