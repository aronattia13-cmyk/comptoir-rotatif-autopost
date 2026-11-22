"""Aperçu (aucune écriture dans la file ni l'historique) du rythme 7 j/7 :
Story Instagram chaque matin + post Instagram/Facebook chaque après-midi,
~5 publicités produits par semaine, rotation des 5 familles, jamais deux fois le même visuel."""
import copy, csv, json, sys
from datetime import date, timedelta
from comptoir import catalog, planner
from comptoir.history import History

STORY_TIME, POST_TIME = "09:30", "14:30"
# Gabarit hebdomadaire (0 = lundi) : 5 produits (3 posts + 2 stories), jamais 2 jours de suite la même famille.
TEMPLATE = {0: ("conseil", "produit"), 1: ("produit", "conseil"), 2: ("conseil", "blog"), 3: ("gravure", "produit"),
            4: ("produit", "conseil"), 5: ("conseil", "produit"), 6: ("conseil", "conseil")}
FAMILIES = ["turbine", "satelec", "ems", "ca-bleu", "ca-rouge"]
LABEL = {"turbine": "Turbines", "satelec": "Détartreurs Satelec/DTE", "ems": "Détartreurs EMS/Woodpecker",
         "ca-bleu": "Contre-angles bleus", "ca-rouge": "Contre-angles rouges"}
LAYOUTS = ["a-blanc", "a-brume", "a-blanc-serif"]
CONSEILS = [  # sujets pédagogiques : texte à rédiger puis valider par David avant activation
    "Bague bleue, bague rouge : ce que signifie la couleur d'un contre-angle",
    "Turbine ou contre-angle multiplicateur : les différences d'usage",
    "Raccord rapide : vérifier la compatibilité avec son unit",
    "Satelec/DTE ou EMS/Woodpecker : identifier son générateur avant de commander",
    "LED ou sans LED : à quoi sert l'éclairage de la pièce à main",
    "Lot de 5 ou lot de 50 : dimensionner son stock de rotatifs",
    "Combien de rotatifs par fauteuil pour tenir le rythme de stérilisation",
    "Entretien quotidien : nettoyage, lubrification, stérilisation (selon la notice)",
    "Pourquoi suivre la notice du fabricant pour la lubrification",
    "Traçabilité des rotatifs : organiser la rotation au cabinet",
    "Signes qu'une turbine doit être contrôlée (bruit, vibrations, chauffe)",
    "Ranger ses pièces à main après stérilisation",
    "Gravure des instruments : retrouver ses rotatifs plus facilement",
    "Préparer une commande de rotatifs : la check-list",
    "Spray : pourquoi vérifier le refroidissement avant chaque séance",
    "Comment lire une fiche produit : rapport, vitesse, raccord, LED",
]

def fam(p):
    k = planner.model_key(p)
    if k.startswith("turbine"): return "turbine"
    if "satelec" in k: return "satelec"
    if "ems" in k: return "ems"
    if "bleue" in k: return "ca-bleu"
    if "rouge" in k: return "ca-rouge"
    return None                           # ex. coupleur : hors rotation demandée

def main(start: date, days: int, out: str):
    prods = [p for p in planner.publishable(catalog.load_catalog()) if fam(p)]
    hist = History.load()
    used = {(e.reference, e.surface, e.layout) for e in hist.entries if e.family == "product" and e.status != "missed"}
    last_fam_use = {f: date(2000, 1, 1) for f in FAMILIES}
    last_prod = {}
    for e in hist.entries:
        if e.family == "product":
            p = next((x for x in prods if x.handle == e.reference), None)
            if p:
                d = date.fromisoformat(e.date); last_fam_use[fam(p)] = max(last_fam_use[fam(p)], d)
                last_prod[p.handle] = max(last_prod.get(p.handle, date(2000, 1, 1)), d)
    rows, prev_day_fams, ci, blog_i = [], set(), 0, 0
    articles = json.load(open("data/articles.json")); articles = articles if isinstance(articles, list) else articles["articles"]
    done_urls = {e.reference for e in hist.entries if e.family == "blog"}          # délai de 90 jours par article
    articles = [a for a in articles if a.get("url") not in done_urls]
    for i in range(days):
        day = start + timedelta(days=i); today_fams = set()
        for surface, kind in zip(("story", "post"), TEMPLATE[day.weekday()]):
            r = {"date": day.isoformat(), "jour": "lun mar mer jeu ven sam dim".split()[day.weekday()],
                 "heure": STORY_TIME if surface == "story" else POST_TIME,
                 "support": "Story Instagram" if surface == "story" else "Post Instagram + Facebook"}
            if kind == "produit":
                # Ordre décalé d'un cran chaque semaine : chaque famille passe tour à tour en post et en story.
                pos = sum(1 for d in range(day.weekday()) for k in TEMPLATE[d] if k == "produit") + (surface == "post" and TEMPLATE[day.weekday()][0] == "produit")
                wk = (day - start).days // 7
                pref = FAMILIES[(pos + wk) % len(FAMILIES)]
                fams = [pref] + sorted([f for f in FAMILIES if f != pref], key=lambda f: last_fam_use[f])
                fams = [f for f in fams if f not in prev_day_fams | today_fams]
                choice = None
                for f in fams:
                    cands = []
                    for p in prods:
                        if fam(p) != f: continue
                        free = [l for l in LAYOUTS if (p.handle, surface, l) not in used]
                        if free: cands.append((last_prod.get(p.handle, date(2000, 1, 1)), p, free[0]))
                    if cands:
                        _, p, lay = min(cands, key=lambda c: (c[0], c[1].handle)); choice = (f, p, lay); break
                if not choice:
                    r.update(type="produit", contenu="AUCUN VISUEL INÉDIT DISPONIBLE : créneau remplacé par un conseil"); rows.append(r); continue
                f, p, lay = choice
                used.add((p.handle, surface, lay)); last_fam_use[f] = day; last_prod[p.handle] = day; today_fams.add(f)
                r.update(type="Publicité produit", famille=LABEL[f], contenu=p.display_name, lot=p.lot_size,
                         prix=f"{p.price} €", led="sans LED" if "san led" in planner.model_key(p) else "LED",
                         visuel=lay, lien=p.url)
            elif kind == "blog" and blog_i < len(articles):
                a = articles[blog_i]; blog_i += 1
                r.update(type="Article du blog", contenu=a.get("title", ""), lien=a.get("url", ""))
            elif kind == "gravure":
                r.update(type="Service (gravure)", contenu="Message gravure (rotation des 4 messages existants)")
            else:
                r.update(type="Conseil / info", contenu=CONSEILS[ci % len(CONSEILS)] + "  [texte à rédiger et valider]"); ci += 1
            rows.append(r)
        prev_day_fams = today_fams
    keys = ["date", "jour", "heure", "support", "type", "famille", "contenu", "lot", "prix", "led", "visuel", "lien"]
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, keys); w.writeheader(); [w.writerow({k: r.get(k, "") for k in keys}) for r in rows]
    json.dump(rows, open(out.replace(".csv", ".json"), "w"), ensure_ascii=False, indent=1)
    return rows

if __name__ == "__main__":
    rows = main(date.fromisoformat(sys.argv[1]), int(sys.argv[2]), sys.argv[3])
    prod = [r for r in rows if r.get("type") == "Publicité produit"]
    print(len(rows), "contenus,", len(prod), "pubs produits")
    for r in prod: print(r["date"], r["jour"], r["support"][:5], r["famille"], "|", r["contenu"], "lot", r["lot"], r["prix"], r["led"], r["visuel"])
