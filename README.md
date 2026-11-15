# comptoir-social

Automated Instagram & Facebook publishing for **Le Comptoir du Rotatif**, built from the Shopify catalogue.
100 % free stack: Shopify public `products.json`, Python + Pillow, GitHub Actions, GitHub Pages, Meta Graph API.

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Catalogue retrieval + 6 layouts × 3 formats | done |
| 2 | Autonomous community manager: products / blog / services, rotation, history, checks | done |
| 2b | Art direction overhaul — Direction A « Clinique » (variants 70/20/10) | done |
| 3 | Meta connection: publishing modules, diagnostics, manual test publication | **in progress** — code done, Meta setup pending |
| 3 | Meta connection, dry-run publishing | to do |
| 4 | Live automatic publishing | only after explicit approval |

## Graphic system — Direction A « Clinique »

`src/comptoir/design.py`. White or very light warm grey, one real product photograph as hero (sharpest full-product shot chosen automatically, never upscaled more than 1.2×), original logo small and never enlarged, Inter Tight for names and prices, Instrument Serif as a discreet accent (blog titles, engraving, optional product variant). Bottom line: name + specs left, lot + price right, one shared baseline. Stories have their own vertical layouts inside Instagram's safe zone. Engraving is typographic unless real photos are added to `assets/services/gravure/`.

Product variants in rotation (`config/settings.json` → `product_variants`): `a-blanc`, `a-brume` (very light grey, product cut out without halo), `a-blanc-serif` (serif title test).

Demo: `python scripts/demo_a.py` → `site/demo_a/`. Direction study (A/B/C): `design/directions.py`.

## Phase 2 — how it runs

| Workflow | When | What |
|---|---|---|
| `weekly.yml` | Sunday ~17:00 Paris | refresh catalogue + blog, plan next 7 days, render images, dashboard, bio page, deploy Pages |
| `due.yml` | hourly, weekdays | TEST mode: safety checks on due items, mark them `simulated` |

Weekly rhythm: Mon product post · Tue engraving story · Wed blog post · Thu product story · Fri product post (other range). Facebook gets Mon/Wed/Fri plus an engraving post with a clickable WhatsApp link.

Anti-repetition (config/settings.json): same product 42 d, same model (lot 5 vs 50) 21 d, same article 90 d, same service message 28 d, no layout reused within the last 2 posts. An empty slot is skipped, never filled with a duplicate.

Editable content (no code):
- `data/content/services.json` — engraving messages (controlled wording)
- `data/content/blog_overrides.json` — short titles, enable/disable an article, article image vs product photo
- `assets/services/gravure/` — drop real photos of engraved instruments here; they replace the product-photo fallback automatically

Outputs on GitHub Pages: `/` dashboard, `/liens/` link-in-bio page (WhatsApp, articles, shop), `/media/` public JPEGs for Meta.

Preview locally: `python scripts/phase2_preview.py --start 2026-11-16` → `site/preview/index.html`.

## Layout

```
src/comptoir/catalog.py   Shopify fetch (products.json, optional Storefront API) + normalisation
src/comptoir/design.py    Graphic system (Direction A)
src/comptoir/media.py     Image/logo download with cache
tests/                    pytest suite (91 tests, Meta fully mocked) + catalogue snapshot
assets/fonts/da           Inter Tight, Instrument Serif (SIL OFL)  ·  assets/logo.png  site logo
site/                     Folder to be served by GitHub Pages (public images for Meta)
```

## Run locally

```bash
pip install -r requirements.txt
python -m pytest -q
python scripts/demo_a.py               # Direction A demo set
```

## Formats & layouts

Formats: post 1080×1350, story 1080×1920. Each product visual contains only: logo, one real product photo, name, lot size, exact price.

## Data rules

- Price copied verbatim from Shopify; a product with a missing/zero price is never rendered.
- Unavailable products and probable duplicates are excluded.
- Lot size read from the title ("Lot de 50 …"), never from the handle.
- Photos are never redrawn: only a uniform white background connected to the edges is removed; otherwise the photo is kept as is.

## Verified platform limits (Nov 2026)

- **Instagram**: Content Publishing API, business account required for Stories; JPEG only; 100 API posts / rolling 24 h; containers expire after 24 h. Image must be at a public URL.
- **GitHub**: Actions free on public repos; Pages free on public repos (private repos need a paid plan); scheduled workflows are disabled after 60 days without repo activity — the publishing workflow will commit its history log, which keeps the repo active.
- **Repo must be public** for the free plan. Code and images are public; tokens live only in GitHub Secrets.

## Phase 3 — Meta

Instagram API with Facebook Login (Page-linked), host `graph.facebook.com`, version in `config/settings.json`.
Token: a **System User** token from the Business portfolio (expiry « Never »); the Page access token is derived at runtime.
Permissions (System User token, Facebook-Login family): `instagram_basic`, `instagram_content_publish`, `pages_show_list`, `pages_read_engagement`, `pages_manage_posts` + `ads_management`, `ads_read` (Meta requires them when Page access comes through the Business portfolio, which is the System User case). No OAuth flow, no redirect URL. Recommended: app setting « Require App Secret » + secret `META_APP_SECRET` (appsecret_proof on every call).

| Workflow | What |
|---|---|
| `meta-check.yml` | diagnostics only: token, permissions, Page, linked IG account, quota, public image; optional unpublished IG container |
| `publish-test.yml` | ONE real publication, only with the exact phrase `JE CONFIRME LA PUBLICATION DE TEST` |

Publishing flow: public image check (200, JPEG, size) → IG container → status poll (FINISHED) → `media_publish` → permalink; Facebook: `/{page}/photos`. Per-platform idempotency; ambiguous timeouts are checked against recent media and otherwise marked `uncertain` (never retried blindly).

## Secrets (never in the repo or in chat)

GitHub → repo → Settings → Secrets and variables → Actions → *New repository secret*:
Secrets: `WHATSAPP_NUMBER`, `META_SYSTEM_USER_TOKEN`, `META_APP_SECRET`, `META_PAGE_ID`, `META_IG_USER_ID` (optional, auto-discovered). Optional: `SHOPIFY_STOREFRONT_TOKEN`.
Variable (not secret): `PUBLIC_BASE_URL` = `https://<account>.github.io/<repo>`.

Live publishing requires a committed `config/LIVE_ENABLED` file — created only after explicit approval (phase 4).
