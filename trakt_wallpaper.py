#!/usr/bin/env python3
"""
Fond d'écran Trakt : un repère par jour du mois, allumé si tu as vu un film ce jour-là.
Trois thèmes : "salle" (sièges de cinéma), "pellicule" (planche contact), "affiche".
Usage : python trakt_wallpaper.py            -> génère l'image et l'applique (Mac)
        python trakt_wallpaper.py --demo     -> données factices, sans Trakt
        python trakt_wallpaper.py --ci       -> mode serveur (GitHub) : images dans le dossier site/
"""

import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
import urllib.request
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

# ============ CONFIGURATION ============
TRAKT_CLIENT_ID = "COLLE_TON_CLIENT_ID_ICI"
TRAKT_USERNAME = "ton_pseudo_trakt"
INCLURE_EPISODES = True    # True = les épisodes de séries comptent aussi
THEME = "salle"            # "salle", "pellicule" ou "affiche"
DOSSIER = Path.home() / "trakt-wallpaper"
VERSION_IPHONE = True      # crée aussi une image pour l'écran verrouillé, dans iCloud Drive
IPHONE_TAILLE = (1179, 2556)   # iPhone 15 Pro
MAC_TAILLE = (2560, 1664)      # utilisée en mode serveur, où l'écran ne peut pas être détecté
# =======================================

# Sur GitHub, les identifiants viennent des « secrets » du dépôt, jamais du fichier
TRAKT_CLIENT_ID = os.environ.get("TRAKT_CLIENT_ID") or TRAKT_CLIENT_ID
TRAKT_USERNAME = os.environ.get("TRAKT_USERNAME") or TRAKT_USERNAME

MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet",
           "août", "septembre", "octobre", "novembre", "décembre"]
JOURS_FR = ["L", "M", "M", "J", "V", "S", "D"]

POLICES = {
    "barlow": "ofl/barlowcondensed/BarlowCondensed-Regular.ttf",
    "barlow_gras": "ofl/barlowcondensed/BarlowCondensed-SemiBold.ttf",
    "barlow_leger": "ofl/barlowcondensed/BarlowCondensed-Light.ttf",
    "serif": "ofl/instrumentserif/InstrumentSerif-Regular.ttf",
    "serif_italique": "ofl/instrumentserif/InstrumentSerif-Italic.ttf",
    "mono_leger": "ofl/ibmplexmono/IBMPlexMono-Light.ttf",
    "mono": "ofl/ibmplexmono/IBMPlexMono-Regular.ttf",
    "mono_moyen": "ofl/ibmplexmono/IBMPlexMono-Medium.ttf",
    "points": "ofl/doto/Doto%5BROND,wght%5D.ttf",   # chiffres en matrice de points
}


# ---------- Utilitaires ----------

def police(nom, taille):
    """Télécharge la police une seule fois (Google Fonts), sinon police système."""
    dossier = DOSSIER / "polices"
    dossier.mkdir(parents=True, exist_ok=True)
    fichier = dossier / Path(POLICES[nom]).name
    if not fichier.exists():
        try:
            url = "https://raw.githubusercontent.com/google/fonts/main/" + POLICES[nom]
            urllib.request.urlretrieve(url, fichier)
        except Exception:
            pass
    if nom == "points" and fichier.exists():
        f = ImageFont.truetype(str(fichier), int(taille))
        f.set_variation_by_axes([100, 700])   # points ronds, assez gras
        return f
    for chemin in [fichier, "/System/Library/Fonts/Supplemental/Futura.ttc",
                   "/System/Library/Fonts/Helvetica.ttc"]:
        try:
            return ImageFont.truetype(str(chemin), int(taille))
        except OSError:
            continue
    return ImageFont.load_default(size=int(taille))


def hexa(c, alpha=255):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4)) + (alpha,)


def degrade_vertical(l, h, haut, bas):
    col = Image.new("RGB", (1, 256))
    a, b = hexa(haut)[:3], hexa(bas)[:3]
    for y in range(256):
        t = y / 255
        col.putpixel((0, y), tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)))
    return col.resize((l, h), Image.BICUBIC).convert("RGBA")


def couper(texte, police_, largeur_max, d):
    """Découpe un texte en lignes qui tiennent dans largeur_max."""
    mots, lignes, ligne = texte.split(), [], ""
    for m in mots:
        essai = (ligne + " " + m).strip()
        if d.textlength(essai, font=police_) <= largeur_max or not ligne:
            ligne = essai
        else:
            lignes.append(ligne)
            ligne = m
    if ligne:
        lignes.append(ligne)
    return lignes


def tronquer(texte, police_, largeur_max, d):
    if d.textlength(texte, font=police_) <= largeur_max:
        return texte
    while texte and d.textlength(texte + "…", font=police_) > largeur_max:
        texte = texte[:-1]
    return texte.rstrip() + "…"


def calendrier(aujourdhui):
    premier = date(aujourdhui.year, aujourdhui.month, 1).weekday()
    nb_jours = monthrange(aujourdhui.year, aujourdhui.month)[1]
    nb_lignes = (premier + nb_jours + 6) // 7
    return premier, nb_jours, nb_lignes


# ---------- Données Trakt ----------

def appel_trakt(chemin, params):
    url = f"https://api.trakt.tv{chemin}?" + "&".join(f"{k}={v}" for k, v in params.items())
    req = urllib.request.Request(url, headers={
        "Content-Type": "application/json", "trakt-api-version": "2",
        "trakt-api-key": TRAKT_CLIENT_ID, "User-Agent": "trakt-wallpaper/2.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r), int(r.headers.get("X-Pagination-Page-Count", "1"))


def historique_mois(annee, mois, type_):
    """Liste des visionnages d'un mois : [{quand, titre, duree, serie}], heure locale."""
    fmt = "%Y-%m-%dT%H:%M:%S.000Z"
    debut = datetime(annee, mois, 1).astimezone(timezone.utc)
    fin = datetime(annee, mois, monthrange(annee, mois)[1], 23, 59, 59).astimezone(timezone.utc)
    liste, page, nb_pages = [], 1, 1
    while page <= nb_pages:
        donnees, nb_pages = appel_trakt(
            f"/users/{TRAKT_USERNAME}/history/{type_}",
            {"start_at": debut.strftime(fmt), "end_at": fin.strftime(fmt),
             "limit": 100, "page": page, "extended": "full"})
        for item in donnees:
            quand = datetime.fromisoformat(item["watched_at"].replace("Z", "+00:00")).astimezone()
            if quand.month != mois:
                continue
            if type_ == "movies":
                titre, serie, duree = item["movie"]["title"], None, item["movie"].get("runtime")
            else:
                titre = serie = item["show"]["title"]
                duree = item["episode"].get("runtime") or item["show"].get("runtime")
            liste.append({"quand": quand.isoformat(), "titre": titre,
                          "duree": duree or 0, "serie": serie})
        page += 1
    return sorted(liste, key=lambda v: v["quand"])


def charger_mois(annee, mois, type_, auj):
    """Les mois passés sont gardés en cache : seul le mois en cours est retéléchargé."""
    cache = DOSSIER / "cache" / f"{type_}_{annee}-{mois:02d}.json"
    passe = (annee, mois) < (auj.year, auj.month)
    if passe and cache.exists():
        return json.loads(cache.read_text())
    liste = historique_mois(annee, mois, type_)
    if passe:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(liste, ensure_ascii=False))
    return liste


def charger_annee(auj, demo=False):
    """Renvoie {(mois, type): liste} pour tous les mois de l'année jusqu'à aujourd'hui."""
    if demo:
        return donnees_demo(auj)
    return {(m, t): charger_mois(auj.year, m, t, auj)
            for m in range(1, auj.month + 1) for t in ("movies", "episodes")}


def donnees_demo(auj):
    films = ["Dune : Deuxième partie", "Le Parrain", "Anatomie d'une chute", "Past Lives",
             "Le Voyage de Chihiro", "Whiplash", "Parasite", "In the Mood for Love", "La Haine",
             "Portrait de la jeune fille en feu", "Drive", "Les Démons de Dorothy"]
    series = ["The Bear", "Severance", "Le Bureau des légendes", "Shogun"]
    rnd = random.Random(auj.year)
    annee = {}
    for m in range(1, auj.month + 1):
        dernier = auj.day if m == auj.month else monthrange(auj.year, m)[1]
        for t in ("movies", "episodes"):
            liste = []
            nb = rnd.randint(6, 14) if t == "movies" else rnd.randint(10, 40)
            for _ in range(nb):
                quand = datetime(auj.year, m, rnd.randint(1, dernier), rnd.randint(18, 23))
                if t == "movies":
                    liste.append({"quand": quand.isoformat(), "titre": rnd.choice(films),
                                  "duree": rnd.randint(95, 165), "serie": None})
                else:
                    sr = rnd.choice(series)
                    liste.append({"quand": quand.isoformat(), "titre": sr,
                                  "duree": rnd.randint(30, 60), "serie": sr})
            annee[(m, t)] = sorted(liste, key=lambda v: v["quand"])
    return annee


def duree_txt(minutes):
    h, m = divmod(minutes, 60)
    return f"{h} h {m:02d}" if h else f"{m} min"


def preparer(annee_donnees, auj):
    """Calcule tout ce que le fond d'écran affiche."""
    types = ["movies"] + (["episodes"] if INCLURE_EPISODES else [])

    # Mois en cours
    du_mois = sorted((v for t in types for v in annee_donnees.get((auj.month, t), [])),
                     key=lambda v: v["quand"])
    vus = {}
    for v in du_mois:
        vus.setdefault(datetime.fromisoformat(v["quand"]).day, []).append(v["titre"])
    stats = duree_txt(sum(v["duree"] for v in du_mois)) if du_mois else ""

    # Année entière
    jours_actifs = set()
    for (m, t), liste in annee_donnees.items():
        if t in types:
            for v in liste:
                jours_actifs.add(datetime.fromisoformat(v["quand"]).date())
    films = [v for (m, t), l in annee_donnees.items() if t == "movies" for v in l]
    episodes = [v for (m, t), l in annee_donnees.items() if t == "episodes" for v in l]
    annee = {
        "jours": jours_actifs,
        "temps": sum(v["duree"] for v in films + episodes),
        "films": len(films),
        "episodes": len(episodes),
        "series": len({v["serie"] for v in episodes}),
    }

    # Détails du mois, pour les thèmes « appareil » et « papier »
    precedent = sorted((v for t in types for v in annee_donnees.get((auj.month - 1, t), [])),
                       key=lambda v: v["quand"]) if auj.month > 1 else None
    jour = auj if auj in jours_actifs else auj - timedelta(days=1)
    serie = 0
    while jour in jours_actifs:
        serie += 1
        jour -= timedelta(days=1)
    annee["mois"] = {
        "liste": [{"jour": datetime.fromisoformat(v["quand"]).day, "titre": v["titre"],
                   "duree": v["duree"], "serie": v.get("serie")} for v in du_mois],
        "films": sum(1 for v in du_mois if not v.get("serie")),
        "episodes": sum(1 for v in du_mois if v.get("serie")),
        "minutes": sum(v["duree"] for v in du_mois),
        "minutes_prec": sum(v["duree"] for v in precedent) if precedent is not None else None,
        "serie": serie,
    }
    # Films de chaque mois de l'année, pour le bandeau du bas
    annee["par_mois"] = {
        m: [{"jour": datetime.fromisoformat(v["quand"]).day, "titre": v["titre"], "duree": v["duree"]}
            for t in types for v in annee_donnees.get((m, t), [])]
        for m in range(1, auj.month + 1)}
    return vus, stats, annee


def dernier_film(vus):
    if not vus:
        return None
    return vus[max(vus)][-1]


# ---------- Thème 1 : la salle ----------

def theme_salle(vus, auj, L, H, stats="", annee=None):
    s = H / 1800
    img = degrade_vertical(L, H, "#2E0C17", "#12050A")
    premier, nb_jours, nb_lignes = calendrier(auj)

    # Écran et faisceau de lumière
    ecran_l, ecran_h = L * 0.42, 150 * s
    ex0, ey0 = (L - ecran_l) / 2, 250 * s
    faisceau = Image.new("RGBA", (L, H), (0, 0, 0, 0))
    fd = ImageDraw.Draw(faisceau)
    fd.polygon([(ex0, ey0 + ecran_h), (ex0 + ecran_l, ey0 + ecran_h),
                (L * 0.92, H * 0.95), (L * 0.08, H * 0.95)], fill=hexa("#F3D9B8", 26))
    faisceau = faisceau.filter(ImageFilter.GaussianBlur(90 * s))
    img = Image.alpha_composite(img, faisceau)

    halo = Image.new("RGBA", (L, H), (0, 0, 0, 0))
    ImageDraw.Draw(halo).rounded_rectangle(
        [ex0, ey0, ex0 + ecran_l, ey0 + ecran_h], radius=6 * s, fill=hexa("#F6E7D2", 90))
    img = Image.alpha_composite(img, halo.filter(ImageFilter.GaussianBlur(40 * s)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([ex0, ey0, ex0 + ecran_l, ey0 + ecran_h], radius=6 * s, fill=hexa("#EFE4D3"))

    f_mois = police("barlow_gras", 92 * s)
    titre = f"{MOIS_FR[auj.month - 1].capitalize()} {auj.year}"
    d.text((L / 2, ey0 + ecran_h / 2), titre, font=f_mois, fill=hexa("#3A1220"), anchor="mm")

    # Sièges
    pas = 138 * s
    siege_l, siege_h = 100 * s, 80 * s
    y_depart = ey0 + ecran_h + 230 * s
    f_num = police("barlow_gras", 30 * s)
    f_rang = police("barlow", 30 * s)
    f_jour = police("barlow", 28 * s)

    for i, j in enumerate(JOURS_FR):
        d.text((L / 2 + (i - 3) * pas, y_depart - 105 * s), j, font=f_jour,
               fill=hexa("#8A4A58"), anchor="mm")

    lueurs = Image.new("RGBA", (L, H), (0, 0, 0, 0))
    ld = ImageDraw.Draw(lueurs)
    positions = {}
    for jour in range(1, nb_jours + 1):
        pos = premier + jour - 1
        col, rang = pos % 7, pos // 7
        dx = (col - 3) / 3
        echelle = 1 + 0.03 * rang
        cx = L / 2 + (col - 3) * pas * echelle
        cy = y_depart + rang * pas * 0.95 - 26 * s * dx * dx
        positions[jour] = (cx, cy, echelle)
        if jour in vus:
            r = (70 + 22 * min(len(vus[jour]), 3)) * s
            ld.ellipse([cx - r, cy - r, cx + r, cy + r], fill=hexa("#F5B547", 70))
    img = Image.alpha_composite(img, lueurs.filter(ImageFilter.GaussianBlur(28 * s)))
    d = ImageDraw.Draw(img)

    for jour, (cx, cy, e) in positions.items():
        w, h = siege_l * e, siege_h * e
        if jour in vus:
            dossier, assise, texte = "#F4B84E", "#C98A2E", "#4A1C0E"
        elif jour > auj.day:
            dossier, assise, texte = "#2C0B14", "#22080F", "#4A1A26"
        else:
            dossier, assise, texte = "#4E1525", "#3A0F1B", "#8A4A58"
        d.rounded_rectangle([cx - w / 2, cy - h / 2, cx + w / 2, cy + h * 0.32],
                            radius=18 * s * e, fill=hexa(dossier))
        d.rounded_rectangle([cx - w * 0.56, cy + h * 0.22, cx + w * 0.56, cy + h * 0.55],
                            radius=10 * s * e, fill=hexa(assise))
        d.text((cx, cy - h * 0.08), str(jour), font=f_num, fill=hexa(texte), anchor="mm")
        if jour == auj.day:
            m = 12 * s
            d.rounded_rectangle([cx - w * 0.56 - m, cy - h / 2 - m, cx + w * 0.56 + m, cy + h * 0.55 + m],
                                radius=22 * s * e, outline=hexa("#EFE4D3"), width=max(2, int(4 * s)))

    for rang in range(nb_lignes):
        e = 1 + 0.03 * rang
        cy = y_depart + rang * pas * 0.95 - 26 * s
        lettre = "ABCDEF"[rang]
        for cote in (-1, 1):
            d.text((L / 2 + cote * 4.25 * pas * e, cy), lettre, font=f_rang,
                   fill=hexa("#6E2C3C"), anchor="mm")

    # Bas : dernière séance
    y_bas = y_depart + nb_lignes * pas * 0.95 + 70 * s
    total = sum(len(v) for v in vus.values())
    f_info = police("barlow", 36 * s)
    dernier = dernier_film(vus)
    if dernier:
        ligne = f"Dernière séance : {tronquer(dernier, f_info, L * 0.4, d)}"
        d.text((L / 2, y_bas), ligne, font=f_info, fill=hexa("#D9C2B0"), anchor="mm")
        d.text((L / 2, y_bas + 50 * s), f"{total} film{'s' if total > 1 else ''} ce mois-ci",
               font=f_info, fill=hexa("#8A4A58"), anchor="mm")
    else:
        d.text((L / 2, y_bas), "Aucune séance ce mois-ci pour l'instant", font=f_info,
               fill=hexa("#8A4A58"), anchor="mm")
    return img


# ---------- Thème 2 : la planche contact ----------

def trait_crayon(d, boite, couleur, epaisseur, graine):
    """Entoure une image comme au crayon gras, avec un tracé un peu irrégulier."""
    rnd = random.Random(graine)
    x0, y0, x1, y1 = boite
    cx, cy, rx, ry = (x0 + x1) / 2, (y0 + y1) / 2, (x1 - x0) / 2, (y1 - y0) / 2
    points = []
    for k in range(0, 400):
        a = -0.6 + k / 400 * (2 * math.pi + 0.9)
        bruit = 1 + 0.03 * math.sin(a * 3 + rnd.random()) + (k / 400) * 0.06
        points.append((cx + rx * bruit * math.cos(a), cy + ry * bruit * math.sin(a)))
    d.line(points, fill=couleur, width=epaisseur, joint="curve")


def theme_pellicule(vus, auj, L, H, stats="", annee=None):
    s = H / 1800
    img = degrade_vertical(L, H, "#F1F3F2", "#DDE2E1")
    d = ImageDraw.Draw(img)
    premier, nb_jours, nb_lignes = calendrier(auj)

    cadre_l, cadre_h = 0.07 * L, 0.07 * L / 1.5
    marge_img = cadre_l * 0.09
    bande = cadre_h * 0.36
    bande_l = 7 * cadre_l + 2 * cadre_l * 0.12
    bande_h = cadre_h + 2 * bande
    ecart = 36 * s
    total_h = nb_lignes * bande_h + (nb_lignes - 1) * ecart
    x_bande = (L - bande_l) / 2
    y0 = (H - total_h) / 2 + 70 * s

    f_titre = police("serif_italique", 120 * s)
    f_cote = police("barlow_gras", 24 * s)
    f_film = police("barlow", 27 * s)
    f_info = police("barlow", 34 * s)
    titre = f"{MOIS_FR[auj.month - 1].capitalize()} {auj.year}"
    d.text((x_bande, y0 - 60 * s), titre, font=f_titre, fill=hexa("#2B2420"), anchor="ls")
    total = sum(len(v) for v in vus.values())
    d.text((x_bande + bande_l, y0 - 66 * s),
           f"{total} film{'s' if total > 1 else ''}, {len(vus)} jour{'s' if len(vus) > 1 else ''}",
           font=f_info, fill=hexa("#6B625C"), anchor="rs")

    rnd = random.Random(auj.year * 100 + auj.month)
    a_entourer = None
    for rang in range(nb_lignes):
        bl, bh = int(bande_l), int(bande_h)
        couche = Image.new("RGBA", (bl, bh), (0, 0, 0, 0))
        cd = ImageDraw.Draw(couche)
        cd.rectangle([0, 0, bl, bh], fill=hexa("#231B17"))
        # Perforations
        trou_l, trou_h = cadre_l * 0.085, bande * 0.34
        n_trous = int(bl / (cadre_l / 4))
        for k in range(n_trous):
            tx = k * cadre_l / 4 + cadre_l / 8 - trou_l / 2
            for ty in (bande * 0.1, bh - bande * 0.1 - trou_h):
                cd.rounded_rectangle([tx, ty, tx + trou_l, ty + trou_h], radius=trou_h * 0.25,
                                     fill=hexa("#E8EBEA"))
        for col in range(7):
            pos = rang * 7 + col
            jour = pos - premier + 1
            fx = cadre_l * 0.12 + col * cadre_l + marge_img / 2
            fy = bande
            boite = [fx, fy, fx + cadre_l - marge_img, fy + cadre_h]
            if not 1 <= jour <= nb_jours:
                cd.rectangle(boite, fill=hexa("#1A1411"))
                continue
            if jour in vus:
                cd.rectangle(boite, fill=hexa("#F6DCA6"))
                titres = vus[jour]
                texte = titres[0] if len(titres) == 1 else f"{titres[0]} +{len(titres) - 1}"
                lignes = couper(texte, f_film, (boite[2] - boite[0]) * 0.84, cd)[:3]
                if len(couper(texte, f_film, (boite[2] - boite[0]) * 0.84, cd)) > 3:
                    lignes[-1] = tronquer(lignes[-1] + "…", f_film, (boite[2] - boite[0]) * 0.84, cd)
                hl = 31 * s
                ty = (boite[1] + boite[3]) / 2 - hl * (len(lignes) - 1) / 2
                for i, lg in enumerate(lignes):
                    cd.text(((boite[0] + boite[2]) / 2, ty + i * hl), lg, font=f_film,
                            fill=hexa("#3A2414"), anchor="mm")
            elif jour > auj.day:
                cd.rectangle(boite, fill=hexa("#2A211C"))
            else:
                cd.rectangle(boite, fill=hexa("#3D312A"))
            # Code de bord, comme sur une vraie pellicule
            cd.text((fx + 4 * s, bh - bande * 0.72), str(jour), font=f_cote,
                    fill=hexa("#E0893A" if jour in vus else "#8C5A34"), anchor="lm")
            if jour == auj.day:
                a_entourer = (rang, boite)
        angle = rnd.uniform(-0.7, 0.7)
        decal = rnd.uniform(-14, 14) * s
        tournee = couche.rotate(angle, resample=Image.BICUBIC, expand=True)
        ombre = Image.new("RGBA", tournee.size, (0, 0, 0, 0))
        ombre.putalpha(tournee.getchannel("A").point(lambda v: v * 0.25))
        ombre = ombre.filter(ImageFilter.GaussianBlur(12 * s))
        px = int(x_bande + decal - (tournee.width - bl) / 2)
        py = int(y0 + rang * (bande_h + ecart) - (tournee.height - bh) / 2)
        img.alpha_composite(ombre, (px + int(6 * s), py + int(10 * s)))
        img.alpha_composite(tournee, (px, py))
        if a_entourer and a_entourer[0] == rang:
            b = a_entourer[1]
            m = 18 * s
            d2 = ImageDraw.Draw(img)
            trait_crayon(d2, [px + b[0] - m, py + b[1] - m, px + b[2] + m, py + b[3] + m],
                         hexa("#D8352A", 235), max(3, int(7 * s)), auj.day)
    return img


# ---------- Thème 3 : l'affiche ----------

def theme_affiche(vus, auj, L, H, stats="", annee=None):
    s = H / 1800
    fond, papier, rouge = "#000000", "#F2EEE6", "#E0302A"
    trait, discret = "#3A3A3A", "#8C8A86"   # anneaux et ligne / textes secondaires
    img = Image.new("RGBA", (L, H), hexa(fond))
    d = ImageDraw.Draw(img)
    premier, nb_jours, nb_lignes = calendrier(auj)

    marge = L * 0.12
    if annee:
        bande_annee(d, auj, annee, marge, L, H * 0.115, s, papier, rouge)
    # Le grand texte est le temps passé devant des films ce mois-ci
    nom = stats or "0 h"
    mois_petit = MOIS_FR[auj.month - 1]
    taille = 420 * s
    f_mois = police("serif_italique", taille)
    while d.textlength(nom, font=f_mois) > L * 0.42:
        taille *= 0.94
        f_mois = police("serif_italique", taille)
    base = H * (0.64 if annee else 0.56)
    d.text((marge, base), nom, font=f_mois, fill=hexa(papier), anchor="ls")

    # Année à gauche, mois à droite, sur la même ligne au-dessus du grand chiffre
    f_an = police("barlow", 44 * s)
    haut_texte = d.textbbox((marge, base), nom, font=f_mois, anchor="ls")[1]
    y_an = haut_texte - 34 * s
    # Pas d'année ni de mois au-dessus : seulement le grand chiffre

    # Grille de points
    pas = 92 * s
    r = 30 * s
    gx = L - marge - 7 * pas
    gy = base - nb_lignes * pas + pas * 0.5 - r
    f_j = police("barlow", 28 * s)
    for i, j in enumerate(JOURS_FR):
        d.text((gx + i * pas + pas / 2, gy - pas * 0.55), j, font=f_j, fill=hexa(discret), anchor="mm")
    for jour in range(1, nb_jours + 1):
        pos = premier + jour - 1
        cx = gx + (pos % 7) * pas + pas / 2
        cy = gy + (pos // 7) * pas + pas / 2
        if jour in vus:
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=hexa(papier))
            for k in range(1, min(len(vus[jour]), 3)):
                rr = r + k * 9 * s
                d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=hexa(papier),
                          width=max(2, int(3 * s)))
        elif jour > auj.day:
            rr = 5 * s
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=hexa(trait))
        else:
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=hexa(trait),
                      width=max(2, int(3 * s)))
        if jour == auj.day:
            rr = r * 0.32
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=hexa(rouge))

    # Ligne de séparation : même écart au-dessus (bas du mois) et en dessous (haut des titres)
    ecart = 64 * s
    bas_mois = d.textbbox((marge, base), nom, font=f_mois, anchor="ls")[3]
    y_ligne = max(bas_mois, base) + ecart
    d.line([(marge, y_ligne), (L - marge, y_ligne)], fill=hexa(trait), width=max(1, int(2 * s)))

    f_gen = police("barlow", 40 * s)
    f_gen_g = police("barlow_gras", 40 * s)
    hauteur_cap = -d.textbbox((0, 0), "D", font=f_gen_g, anchor="ls")[1]
    y = y_ligne + ecart + hauteur_cap

    largeur = L - 2 * marge
    titres = [(j, t) for j in sorted(vus) for t in vus[j]]
    if titres:
        x = marge
        for jour, t in titres:
            etiquette = f"{jour} "
            w = d.textlength(etiquette, font=f_gen) + d.textlength(t, font=f_gen_g) + 46 * s
            if x + w > marge + largeur:
                x = marge
                y += 58 * s
                if y > H * 0.88:
                    break
            d.text((x, y), etiquette, font=f_gen, fill=hexa(discret), anchor="ls")
            x2 = x + d.textlength(etiquette, font=f_gen)
            d.text((x2, y), t, font=f_gen_g, fill=hexa(rouge if jour == auj.day else papier), anchor="ls")
            x += w
    return img


def bande_annee(d, auj, annee, marge, L, y0, s, papier, rouge, r=None, taille_info=25,
                police_info="barlow_leger", opacite_info=0.36, ecart_info=36, avec_infos=True):
    """Une colonne par semaine, une ligne par jour : toute l'année en points.
    Renvoie la position du bas de la bande (ligne d'info comprise)."""
    debut = date(auj.year, 1, 1)
    nb = 366 if monthrange(auj.year, 2)[1] == 29 else 365
    decalage = debut.weekday()
    nb_col = (decalage + nb + 6) // 7
    largeur = L - 2 * marge
    pas = largeur / nb_col
    r = r or 5 * s                 # même petit point que les jours à venir du mois

    def melange(c, t):
        a, b = hexa(c)[:3], (0, 0, 0)
        return tuple(int(x * t + y * (1 - t)) for x, y in zip(a, b)) + (255,)

    eteint, futur = melange(papier, 0.42), hexa("#3A3A3A")
    for k in range(nb):
        jour = date.fromordinal(debut.toordinal() + k)
        pos = decalage + k
        cx = marge + (pos // 7) * pas + pas / 2
        cy = y0 + (pos % 7) * pas + pas / 2
        if jour == auj:
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=hexa(rouge))
        elif jour in annee["jours"]:
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=hexa(papier))
        elif jour < auj:
            # Comme dans la grille du mois : simple contour pour les jours sans film
            ro = r + 1.5 * s
            d.ellipse([cx - ro, cy - ro, cx + ro, cy + ro], outline=eteint, width=max(2, int(2 * s)))
        else:
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=futur)

    if not avec_infos:
        return y0 + 7 * pas
    # Ligne d'info sous la bande, dans le style de l'année
    morceaux = [duree_txt(annee["temps"]) if annee["temps"] else None,
                f"{annee['films']} film{'s' if annee['films'] > 1 else ''}"]
    if annee["episodes"]:
        morceaux.append(f"{annee['episodes']} épisode{'s' if annee['episodes'] > 1 else ''}"
                        f" de {annee['series']} série{'s' if annee['series'] > 1 else ''}")
    f_info = police(police_info, taille_info * s)
    tres_discret = melange(papier, opacite_info)
    x = marge + pas / 2 - r
    y = y0 + 7 * pas + ecart_info * s
    for txt in [m for m in morceaux if m]:
        d.text((x, y), txt, font=f_info, fill=tres_discret, anchor="ls")
        x += d.textlength(txt, font=f_info) + 36 * s
    return y


def iphone_affiche(vus, auj, L, H, stats="", annee=None):
    """Même affiche, en vertical pour l'écran verrouillé de l'iPhone.
    Le haut est laissé libre pour l'heure, le bas pour les boutons."""
    s = L / 1290
    fond, papier, rouge = "#000000", "#F2EEE6", "#E0302A"
    trait, discret = "#3A3A3A", "#8C8A86"
    img = Image.new("RGBA", (L, H), hexa(fond))
    d = ImageDraw.Draw(img)
    premier, nb_jours, nb_lignes = calendrier(auj)
    marge = L * 0.09
    largeur = L - 2 * marge

    # Temps du mois en grand, avec l'année et le mois au-dessus
    nom = stats or "0 h"
    mois_petit = MOIS_FR[auj.month - 1]
    taille = 330 * s
    f_mois = police("serif_italique", taille)
    while d.textlength(nom, font=f_mois) > largeur * 0.98:
        taille *= 0.95
        f_mois = police("serif_italique", taille)
    f_an = police("barlow", 42 * s)
    y_an = H * 0.31
    hauteur = -d.textbbox((0, 0), nom, font=f_mois, anchor="ls")[1]
    base = y_an + 34 * s + hauteur
    # Sur iPhone : pas d'année ni de mois au-dessus, seulement le grand chiffre
    if stats:   # rien d'affiché tant qu'aucun film n'a été vu ce mois-ci
        d.text((marge, base), nom, font=f_mois, fill=hexa(papier), anchor="ls")
    bas_mois = max(base, d.textbbox((marge, base), nom, font=f_mois, anchor="ls")[3])

    # Grille du mois sur toute la largeur
    pas_x, pas_y, r = largeur / 7, 92 * s, 30 * s
    f_j = police("barlow", 30 * s)
    y_lettres = bas_mois + 90 * s
    for i, j in enumerate(JOURS_FR):
        d.text((marge + i * pas_x + pas_x / 2, y_lettres), j, font=f_j, fill=hexa(discret), anchor="mm")
    gy = y_lettres + 30 * s
    for jour in range(1, nb_jours + 1):
        pos = premier + jour - 1
        cx = marge + (pos % 7) * pas_x + pas_x / 2
        cy = gy + (pos // 7) * pas_y + pas_y / 2
        if jour in vus:
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=hexa(papier))
            for k in range(1, min(len(vus[jour]), 3)):
                rr = r + k * 9 * s
                d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=hexa(papier), width=max(2, int(3 * s)))
        elif jour > auj.day:
            rr = 5 * s
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=hexa(trait))
        else:
            d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=hexa(trait), width=max(2, int(3 * s)))
        if jour == auj.day:
            rr = r * 0.32
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=hexa(rouge))

    # Séparation et liste des films (3 lignes au maximum, les plus récents en priorité)
    ecart = 50 * s
    y_ligne = gy + nb_lignes * pas_y + 20 * s
    d.line([(marge, y_ligne), (L - marge, y_ligne)], fill=hexa(trait), width=max(1, int(2 * s)))
    f_gen = police("barlow", 36 * s)
    f_gen_g = police("barlow_gras", 36 * s)
    cap = -d.textbbox((0, 0), "D", font=f_gen_g, anchor="ls")[1]
    titres = [(j, t) for j in sorted(vus) for t in vus[j]]

    def lignes_pour(liste):
        lignes, ligne, x = [], [], 0
        for jour, t in liste:
            w = d.textlength(f"{jour} ", font=f_gen) + d.textlength(t, font=f_gen_g) + 38 * s
            if x + w > largeur and ligne:
                lignes.append(ligne)
                ligne, x = [], 0
            ligne.append((jour, t))
            x += w
        if ligne:
            lignes.append(ligne)
        return lignes

    while titres and len(lignes_pour(titres)) > 3:
        titres = titres[1:]          # on retire les plus anciens
    y = y_ligne + ecart + cap
    if titres:
        for ligne in lignes_pour(titres):
            x = marge
            for jour, t in ligne:
                d.text((x, y), f"{jour} ", font=f_gen, fill=hexa(discret), anchor="ls")
                x2 = x + d.textlength(f"{jour} ", font=f_gen)
                d.text((x2, y), t, font=f_gen_g, fill=hexa(rouge if jour == auj.day else papier), anchor="ls")
                x = x2 + d.textlength(t, font=f_gen_g) + 38 * s
            y += 54 * s

    # Bande de l'année en bas, au-dessus des boutons de l'écran verrouillé
    if annee:
        nb_col = 53
        pas_b = largeur / nb_col
        bas_bande = H * 0.855
        bande_annee(d, auj, annee, marge, L, bas_bande - 7 * pas_b, s, papier, rouge,
                    r=pas_b * 0.16, avec_infos=False)
    return img


# ---------- Outils communs aux thèmes « appareil » et « papier » ----------

MOIS_COURT = ["JANV.", "FÉVR.", "MARS", "AVR.", "MAI", "JUIN", "JUIL.", "AOÛT", "SEPT.",
              "OCT.", "NOV.", "DÉC."]
JOURS_LONG = ["LUNDI", "MARDI", "MERCREDI", "JEUDI", "VENDREDI", "SAMEDI", "DIMANCHE"]


def espace(d, xy, texte, police_, fill, esp, anchor="ls"):
    """Texte avec espacement entre les lettres. Renvoie la position x de fin."""
    x, y = xy
    if anchor[0] in "mr":
        w = largeur_espace(d, texte, police_, esp)
        x -= w / 2 if anchor[0] == "m" else w
    for c in texte:
        d.text((x, y), c, font=police_, fill=fill, anchor="l" + anchor[1])
        x += d.textlength(c, font=police_) + esp
    return x - esp


def largeur_espace(d, texte, police_, esp):
    return sum(d.textlength(c, font=police_) for c in texte) + esp * max(0, len(texte) - 1)


def heures_points(minutes):
    h, m = divmod(minutes, 60)
    return f"{h}:{m:02d}"


def ecart_txt(mois):
    """Écart avec le mois précédent, ex. « +3 H 10 »."""
    if mois["minutes_prec"] is None:
        return None
    diff = mois["minutes"] - mois["minutes_prec"]
    h, m = divmod(abs(diff), 60)
    return ("+" if diff >= 0 else "−") + (f"{h} H {m:02d}" if h else f"{m} MIN")


def ombre(img, boite, rayon, flou, dy, opacite):
    """Ombre portée douce sous un rectangle arrondi (calculée en basse définition)."""
    k = 4
    calque = Image.new("L", (img.width // k, img.height // k), 0)
    x0, y0, x1, y1 = boite
    ImageDraw.Draw(calque).rounded_rectangle(
        [x0 / k, (y0 + dy) / k, x1 / k, (y1 + dy) / k], rayon / k, fill=opacite)
    calque = calque.filter(ImageFilter.GaussianBlur(flou / k)).resize(img.size, Image.BILINEAR)
    noir = Image.new("RGBA", img.size, (0, 0, 0, 255))
    noir.putalpha(calque)
    return Image.alpha_composite(img, noir)


def bloc_degrade(img, boite, rayon, haut, bas):
    """Rectangle arrondi rempli d'un dégradé vertical."""
    x0, y0, x1, y1 = [int(v) for v in boite]
    grad = degrade_vertical(x1 - x0, y1 - y0, haut, bas)
    masque = Image.new("L", grad.size, 0)
    ImageDraw.Draw(masque).rounded_rectangle([0, 0, grad.width - 1, grad.height - 1], rayon, fill=255)
    img.paste(grad, (x0, y0), masque)


def grain(img, force):
    """Grain photo, appliqué après réduction pour qu'il reste visible."""
    if not force:
        return img
    bruit = Image.effect_noise(img.size, 64)
    plus = bruit.point(lambda v: int(max(0, v - 128) * force / 64)).convert("RGB")
    moins = bruit.point(lambda v: int(max(0, 128 - v) * force / 64)).convert("RGB")
    return ImageChops.subtract(ImageChops.add(img.convert("RGB"), plus), moins)


def bande_points(d, auj, jours, x0, x1, y0, r, vu, vide, futur, today, contour=None):
    """L'année en points : une colonne par semaine, une ligne par jour. Renvoie le bas."""
    debut = date(auj.year, 1, 1)
    nb = 366 if monthrange(auj.year, 2)[1] == 29 else 365
    decalage = debut.weekday()
    pas = (x1 - x0) / ((decalage + nb + 6) // 7)
    for k in range(nb):
        jour = date.fromordinal(debut.toordinal() + k)
        pos = decalage + k
        cx, cy = x0 + (pos // 7) * pas + pas / 2, y0 + (pos % 7) * pas + pas / 2
        if jour == auj:
            d.ellipse([cx - r * 1.3, cy - r * 1.3, cx + r * 1.3, cy + r * 1.3], fill=hexa(today))
        elif jour in jours:
            d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=hexa(vu))
        elif jour < auj:
            if contour:
                d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=hexa(vide), width=contour)
            else:
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=hexa(vide))
        else:
            rr = r * 0.55
            d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=hexa(futur))
    return y0 + 7 * pas


# ---------- Thème 4 : l'appareil (écran noir, accent orange) ----------

ORANGE = "#FF4F12"


def ecran_appareil(img, boite, vus, auj, annee, s, vertical=False):
    """Contenu de l'écran : date, compteur, frise du mois, calendrier, année."""
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = boite
    w = x1 - x0
    blanc, gris, sombre = "#ECE8E0", "#77736C", "#2A2826"
    mois = annee["mois"]
    p = w * (0.075 if vertical else 0.06)
    xa, xb = x0 + p, x1 - p
    premier, nb_jours, nb_lignes = calendrier(auj)

    # Date du jour
    f_date = police("mono_moyen", (44 if vertical else 30) * s)
    y = y0 + p + (44 if vertical else 30) * s
    espace(d, (xa, y), f"{JOURS_LONG[auj.weekday()]} {auj.day:02d}", f_date, hexa(blanc), 4 * s)
    espace(d, (xb, y), f"{MOIS_FR[auj.month - 1].upper()} {auj.year}", f_date, hexa(gris), 4 * s, "rs")

    # Compteur du mois en matrice de points
    h_txt, m_txt = heures_points(mois["minutes"]).split(":")
    largeur_max = (xb - xa) * (0.92 if vertical else 0.6)
    taille = (360 if vertical else 340) * s

    def largeur_compteur(f):
        haut_ = -d.textbbox((0, 0), "0", font=f, anchor="ls")[1]
        return d.textlength(h_txt + m_txt, font=f) + haut_ * 0.45, haut_
    f_gros = police("points", taille)
    while largeur_compteur(f_gros)[0] > largeur_max:
        taille *= 0.95
        f_gros = police("points", taille)
    lc, haut = largeur_compteur(f_gros)
    base = y + 50 * s + haut
    d.text((xa, base), h_txt, font=f_gros, fill=hexa(blanc), anchor="ls")
    xc = xa + d.textlength(h_txt, font=f_gros) + haut * 0.225
    rp = haut * 0.075
    for yy in (base - haut * 0.3, base - haut * 0.7):
        d.ellipse([xc - rp, yy - rp, xc + rp, yy + rp], fill=hexa(ORANGE))
    d.text((xc + haut * 0.225, base), m_txt, font=f_gros, fill=hexa(blanc), anchor="ls")
    fin = xa + lc
    f_pt = police("mono_moyen", (30 if vertical else 24) * s)
    espace(d, (fin + 22 * s, base - haut + 26 * s), "H", f_pt, hexa(ORANGE), 0)
    espace(d, (fin + 22 * s, base - haut + 62 * s), "CE MOIS", f_pt, hexa(gris), 3 * s)

    # Calendrier du mois en points (à droite sur Mac, sous la frise sur iPhone)
    def calendrier_points(cx0, cy0, pas):
        f_j = police("mono", pas * 0.32)
        for i, j in enumerate(JOURS_FR):
            d.text((cx0 + i * pas + pas / 2, cy0 - pas * 0.25), j, font=f_j,
                   fill=hexa(gris), anchor="ms")
        for jour in range(1, nb_jours + 1):
            pos = premier + jour - 1
            cx, cy = cx0 + (pos % 7) * pas + pas / 2, cy0 + (pos // 7) * pas + pas / 2
            r = pas * 0.26
            if jour == auj.day:
                d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=hexa(blanc), width=max(2, int(4 * s)))
                if jour in vus:
                    rr = r * 0.55
                    d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=hexa(ORANGE))
            elif jour in vus:
                d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=hexa(ORANGE))
            elif jour < auj.day:
                rr = r * 0.42
                d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=hexa(gris))
            else:
                rr = r * 0.3
                d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=hexa(sombre))
        return cy0 + nb_lignes * pas

    if not vertical:
        pas_c = (base - y + 10 * s) / (nb_lignes + 0.3)
        calendrier_points(xb - 7 * pas_c, y + 0.3 * pas_c + 40 * s, pas_c)

    # Ligne « films » + pastille orange avec l'écart au mois précédent
    y = base + (70 if vertical else 56) * s
    f_lbl = police("mono_moyen", (32 if vertical else 26) * s)
    nb = len(mois["liste"])
    espace(d, (xa, y), f"{nb} FILM{'S' if nb > 1 else ''}", f_lbl, hexa(blanc), 3 * s)
    ecart = ecart_txt(mois)
    if ecart:
        lib = f"{ecart} VS {MOIS_COURT[auj.month - 2]}"
        lw = largeur_espace(d, lib, f_lbl, 3 * s)
        hb = 46 * s if vertical else 38 * s
        d.rounded_rectangle([xb - lw - 30 * s, y - hb * 0.78, xb, y + hb * 0.32], 9 * s, fill=hexa(ORANGE))
        espace(d, (xb - 15 * s, y), lib, f_lbl, hexa("#0A0A0A"), 3 * s, "rs")

    # Frise orange : une graduation par jour, un trait noir épais les jours de film
    bh = (300 if vertical else 290) * s
    fy0 = y + 34 * s
    fy1 = fy0 + bh
    d.rounded_rectangle([xa, fy0, xb, fy1], 34 * s, fill=hexa(ORANGE))
    fx0, fx1 = xa + 36 * s, xb - 36 * s
    pas_f = (fx1 - fx0) / nb_jours
    mid = (fy0 + fy1) / 2
    noir = hexa("#0A0A0A")
    for k in range(nb_jours * 4 + 1):
        x = fx0 + k * pas_f / 4
        hh = 9 * s if k % 4 == 0 else 5 * s
        if k / 4 <= auj.day:
            d.line([(x, mid - hh), (x, mid + hh)], fill=noir, width=max(1, int(2.5 * s)))
        else:
            d.line([(x, mid - 2 * s), (x, mid + 2 * s)], fill=hexa("#0A0A0A", 110), width=max(1, int(2 * s)))
    f_h = police("mono_moyen", (26 if vertical else 22) * s)
    f_t = police("mono_moyen", (26 if vertical else 22) * s)
    fin_etiq = -1e9
    for jour in sorted(vus):
        bx0 = fx0 + (jour - 1) * pas_f
        bx1 = bx0 + pas_f * min(4, 1 + 0.6 * (len(vus[jour]) - 1))
        ep = 7 * s
        d.rectangle([bx0, mid - ep, bx1, mid + ep], fill=noir)
    # Étiquettes : jour au-dessus, titre en dessous, sans chevauchement (les plus récents d'abord)
    places = []
    for jour in sorted(vus, reverse=True):
        bx0 = fx0 + (jour - 1) * pas_f
        titre = tronquer(vus[jour][-1].upper(), f_t, (fx1 - fx0) * (0.42 if vertical else 0.28), d)
        lw = max(d.textlength(titre, font=f_t), d.textlength("00", font=f_h)) + 2 * s * len(titre)
        if bx0 + lw > fx1:
            continue
        if any(not (bx0 + lw + 24 * s < a or bx0 > b + 24 * s) for a, b in places):
            continue
        places.append((bx0, bx0 + lw))
        espace(d, (bx0, mid - 24 * s), f"{jour:02d}", f_h, noir, 2 * s)
        espace(d, (bx0, mid + 24 * s + 18 * s), titre, f_t, noir, 2 * s)
    # Repère d'aujourd'hui
    tx = fx0 + (auj.day - 0.5) * pas_f
    d.polygon([(tx - 9 * s, fy0), (tx + 9 * s, fy0), (tx, fy0 + 13 * s)], fill=noir)

    y = fy1
    if vertical:
        pas_c = (xb - xa) / 7 * 0.45
        y = calendrier_points(xa + ((xb - xa) - 7 * pas_c) / 2, y + 100 * s, pas_c)

    # L'année en points, puis les chiffres de l'année
    y_an = y + (70 if vertical else 80) * s
    bas = bande_points(d, auj, annee["jours"], xa, xb, y_an, (xb - xa) / 53 * 0.22,
                       blanc, "#4A4743", "#2A2826", ORANGE)
    f_inf = police("mono", (26 if vertical else 21) * s)
    morceaux = [f"{auj.year}", duree_txt(annee["temps"]).upper() if annee["temps"] else None,
                f"{annee['films']} FILMS"]
    if annee["episodes"]:
        morceaux.append(f"{annee['episodes']} ÉP.")
    if mois["serie"] > 1:
        morceaux.append(f"SÉRIE {mois['serie']} J")
    x = xa
    for m in [m for m in morceaux if m]:
        x = espace(d, (x, bas + 44 * s), m, f_inf, hexa(gris), 2 * s) + 36 * s


def theme_appareil(vus, auj, L, H, stats="", annee=None):
    s = H / 1800
    img = Image.new("RGBA", (L, H), hexa("#0E0E0E"))
    halo = Image.new("L", (L // 8, H // 8), 0)
    ImageDraw.Draw(halo).ellipse([L / 8 * 0.2, H / 8 * 0.1, L / 8 * 0.8, H / 8 * 0.9], fill=60)
    halo = halo.filter(ImageFilter.GaussianBlur(L / 8 * 0.12)).resize((L, H), Image.BILINEAR)
    clair = Image.new("RGBA", (L, H), hexa("#3A3632"))
    clair.putalpha(halo)
    img = Image.alpha_composite(img, clair)

    # Le boîtier
    bw, bh = L * 0.64, H * 0.68
    bx0, by0 = (L - bw) / 2, (H - bh) / 2 + 10 * s
    bx1, by1 = bx0 + bw, by0 + bh
    rb = 70 * s
    img = ombre(img, [bx0, by0, bx1, by1], rb, 90 * s, 50 * s, 220)
    bloc_degrade(img, [bx0, by0, bx1, by1], rb, "#A6A29B", "#7F7B75")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([bx0, by0, bx1, by1], rb, outline=hexa("#C9C5BE", 140), width=max(2, int(3 * s)))

    # L'écran
    m = 34 * s
    sx1 = bx0 + bw * 0.78
    ecran = [bx0 + m, by0 + m, sx1, by1 - m]
    d.rounded_rectangle(ecran, 44 * s, fill=hexa("#050505"))
    d.rounded_rectangle(ecran, 44 * s, outline=hexa("#3D3A36"), width=max(2, int(4 * s)))
    ecran_appareil(img, ecran, vus, auj, annee, s)
    d = ImageDraw.Draw(img)

    # Colonne de commandes
    cx0, cx1 = sx1, bx1
    cmid = (cx0 + cx1) / 2
    for yy in (by0 + bh * 0.2, by0 + bh * 0.8):
        d.line([(cx0, yy), (bx1, yy)], fill=hexa("#5E5A55"), width=max(2, int(3 * s)))
        d.line([(cx0, yy + 3 * s), (bx1, yy + 3 * s)], fill=hexa("#BAB6AF"), width=max(1, int(2 * s)))
    # Damier de clap, une case orange
    c = 22 * s
    dx0, dy0 = cmid - 2 * c, by0 + bh * 0.1 - c
    for i in range(4):
        for j in range(2):
            if (i + j) % 2 == 0:
                d.rectangle([dx0 + i * c, dy0 + j * c, dx0 + (i + 1) * c, dy0 + (j + 1) * c],
                            fill=hexa(ORANGE if (i, j) == (0, 0) else "#141414"))
    # Deux touches empilées
    tw, th = (cx1 - cx0) * 0.42, bh * 0.12
    tx0, ty0 = cmid - tw / 2, by0 + bh * 0.27
    img = ombre(img, [tx0, ty0, tx0 + tw, ty0 + 2 * th], tw / 2, 20 * s, 14 * s, 160)
    bloc_degrade(img, [tx0, ty0, tx0 + tw, ty0 + 2 * th], tw / 2, "#E2DFD9", "#B3AFA8")
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([tx0, ty0, tx0 + tw, ty0 + 2 * th], tw / 2, outline=hexa("#5A5651"), width=max(2, int(3 * s)))
    d.line([(tx0, ty0 + th), (tx0 + tw, ty0 + th)], fill=hexa("#5A5651"), width=max(2, int(3 * s)))
    e = 13 * s
    for yy, sens in ((ty0 + th * 0.5, -1), (ty0 + th * 1.5, 1)):
        d.line([(cmid - e, yy - sens * e / 2), (cmid, yy + sens * e / 2), (cmid + e, yy - sens * e / 2)],
               fill=hexa("#3A3733"), width=max(2, int(4 * s)), joint="curve")
    # Bouton lecture orange
    rr = (cx1 - cx0) * 0.25
    oy = by0 + bh * 0.655
    img = ombre(img, [cmid - rr, oy - rr, cmid + rr, oy + rr], rr, 22 * s, 16 * s, 170)
    rond = degrade_vertical(int(2 * rr), int(2 * rr), "#FF7440", "#D9400E")
    masque = Image.new("L", rond.size, 0)
    ImageDraw.Draw(masque).ellipse([0, 0, rond.width - 1, rond.height - 1], fill=255)
    img.paste(rond, (int(cmid - rr), int(oy - rr)), masque)
    d = ImageDraw.Draw(img)
    d.ellipse([cmid - rr, oy - rr, cmid + rr, oy + rr], outline=hexa("#8A2A08"), width=max(2, int(3 * s)))
    for k in range(48):
        a = 2 * math.pi * k / 48
        px, py = cmid + rr * 0.82 * math.cos(a), oy + rr * 0.82 * math.sin(a)
        d.ellipse([px - 2 * s, py - 2 * s, px + 2 * s, py + 2 * s], fill=hexa("#8A2A08"))
    t = rr * 0.34
    d.polygon([(cmid - t * 0.6, oy - t), (cmid - t * 0.6, oy + t), (cmid + t, oy)], fill=hexa("#141414"))
    # Nom du modèle
    f_nom = police("mono_moyen", 24 * s)
    espace(d, (cmid, by0 + bh * 0.9 + 8 * s), "CINÉMÈTRE", f_nom, hexa("#2A2826"), 3 * s, "ms")
    return img


def iphone_appareil(vus, auj, L, H, stats="", annee=None):
    s = L / 1290
    img = Image.new("RGBA", (L, H), hexa("#050505"))
    ecran_appareil(img, [0, H * 0.335, L, H], vus, auj, annee, s, vertical=True)
    return img


# ---------- Thème 5 : le papier (orbe de couleur, schéma technique) ----------

TEINTES = ["#1F3FD1", "#E8452C", "#F5A623", "#0F8B8D", "#7B3FE4", "#E85D9E", "#3BA55C", "#6FC3E8",
           "#2C2A6B"]
ENCRE, GRIS_P = "#151515", "#8F8B85"


def couleurs_mois(liste):
    """Une couleur par film, toujours la même pour un titre, sans doublon dans le mois
    tant qu'il reste des teintes libres."""
    couleurs, prises = {}, []
    for v in liste:
        if v["titre"] in couleurs:
            continue
        i = int(hashlib.md5(v["titre"].encode()).hexdigest(), 16) % len(TEINTES)
        for k in range(len(TEINTES)):
            if TEINTES[(i + k) % len(TEINTES)] not in prises:
                i = (i + k) % len(TEINTES)
                break
        couleurs[v["titre"]] = TEINTES[i]
        prises = (prises + [TEINTES[i]])[-(len(TEINTES) - 1):]
    return couleurs


def orbe(liste, nb_jours, diametre):
    """Chaque film est une tache de couleur : sa place = le jour, sa taille = la durée."""
    c = 420
    toile = Image.new("RGBA", (c, c), hexa("#D6D2CB"))
    dd = ImageDraw.Draw(toile)
    couleurs = couleurs_mois(liste)
    echelle = min(1.0, (5 / max(1, len(liste))) ** 0.3)
    for i, f in enumerate(sorted(liste, key=lambda f: -f["duree"])):
        a = 2 * math.pi * (f["jour"] - 0.5) / nb_jours - math.pi / 2
        rayon = c * (0.17 if len(liste) <= 6 or i % 2 else 0.08)
        px, py = c / 2 + rayon * math.cos(a), c / 2 + rayon * math.sin(a)
        r = c * (0.12 + 0.16 * min(1.0, (f["duree"] or 90) / 180)) * echelle
        dd.ellipse([px - r, py - r, px + r, py + r], fill=hexa(couleurs[f["titre"]]))
    toile = toile.filter(ImageFilter.GaussianBlur(c * (0.075 if len(liste) <= 6 else 0.05)))
    masque = Image.new("L", (c, c), 0)
    ImageDraw.Draw(masque).ellipse([c * 0.15, c * 0.15, c * 0.85, c * 0.85], fill=255)
    masque = masque.filter(ImageFilter.GaussianBlur(c * 0.03))
    halo = masque.filter(ImageFilter.GaussianBlur(c * 0.07)).point(lambda v: int(v * 0.45))
    sortie = Image.new("RGBA", (c, c), (0, 0, 0, 0))
    flou = toile.filter(ImageFilter.GaussianBlur(c * 0.06))
    flou.putalpha(halo)
    sortie = Image.alpha_composite(sortie, flou)
    toile.putalpha(masque)
    sortie = Image.alpha_composite(sortie, toile)
    return sortie.resize((int(diametre), int(diametre)), Image.BICUBIC)


def cadran(d, cx, cy, R, vus, auj, nb_jours, s):
    """Anneau gradué autour de l'orbe : un trait par jour, comme un instrument."""
    lw = max(1, int(2 * s))
    d.arc([cx - R, cy - R, cx + R, cy + R], 0, 360, fill=hexa("#BDB9B2"), width=lw)
    fin = -90 + 360 * auj.day / nb_jours
    d.arc([cx - R, cy - R, cx + R, cy + R], -90, fin, fill=hexa(ENCRE), width=max(2, int(4 * s)))
    f_n = police("mono", 19 * s)
    for jour in range(1, nb_jours + 1):
        a = 2 * math.pi * (jour - 0.5) / nb_jours - math.pi / 2
        co, si = math.cos(a), math.sin(a)
        if jour in vus:
            r0, r1, col, ep = R + 10 * s, R + 40 * s, ENCRE, max(2, int(5 * s))
        else:
            r0, r1, col, ep = R + 10 * s, R + 22 * s, "#A9A59E" if jour <= auj.day else "#CFCBC4", lw
        d.line([(cx + r0 * co, cy + r0 * si), (cx + r1 * co, cy + r1 * si)], fill=hexa(col), width=ep)
        if jour in (1, 10, 20):
            rt = R + 70 * s
            d.text((cx + rt * co, cy + rt * si), f"{jour:02d}", font=f_n, fill=hexa(GRIS_P), anchor="mm")
    # Bulle sur aujourd'hui
    a = 2 * math.pi * (auj.day - 0.5) / nb_jours - math.pi / 2
    co, si = math.cos(a), math.sin(a)
    rb = 30 * s
    r_tige0, r_tige1 = R + 44 * s, R + 92 * s
    bxc, byc = cx + (r_tige1 + rb) * co, cy + (r_tige1 + rb) * si
    d.line([(cx + r_tige0 * co, cy + r_tige0 * si), (cx + r_tige1 * co, cy + r_tige1 * si)],
           fill=hexa(ENCRE), width=lw)
    d.ellipse([bxc - rb, byc - rb, bxc + rb, byc + rb], fill=hexa("#ECEBE7"), outline=hexa(ENCRE), width=lw)
    d.text((bxc, byc), f"{auj.day:02d}", font=police("mono_moyen", 21 * s), fill=hexa(ENCRE), anchor="mm")
    d.ellipse([cx + R * co - 6 * s, cy + R * si - 6 * s, cx + R * co + 6 * s, cy + R * si + 6 * s],
              fill=hexa("#ECEBE7"), outline=hexa(ENCRE), width=lw)


def fond_papier(L, H, s):
    img = Image.new("RGBA", (L, H), hexa("#E6E5E1"))
    d = ImageDraw.Draw(img)
    pas, r = 30 * s, 1.7 * s
    y = pas / 2
    while y < H:
        x = pas / 2
        while x < L:
            d.ellipse([x - r, y - r, x + r, y + r], fill=hexa("#CBC8C2"))
            x += pas
        y += pas
    return img


def pastille(d, x, y, gauche, droite, f, s, sombre=True):
    """Pilule noire avec une petite pilule intérieure, comme une étiquette de prix."""
    h = 64 * s
    w1 = d.textlength(gauche, font=f)
    w2 = d.textlength(droite, font=f) if droite else 0
    largeur = 28 * s + w1 + (20 * s + w2 + 28 * s if droite else 0) + 22 * s
    d.rounded_rectangle([x, y, x + largeur, y + h], h / 2, fill=hexa(ENCRE))
    d.text((x + 26 * s, y + h / 2), gauche, font=f, fill=hexa("#F2F1ED"), anchor="lm")
    if droite:
        ix0 = x + 26 * s + w1 + 18 * s
        d.rounded_rectangle([ix0, y + 9 * s, ix0 + w2 + 28 * s, y + h - 9 * s], (h - 18 * s) / 2,
                            fill=hexa("#F2F1ED" if not sombre else "#4A4845"))
        d.text((ix0 + 14 * s, y + h / 2), droite, font=f,
               fill=hexa(ENCRE if not sombre else "#F2F1ED"), anchor="lm")
    return x + largeur


def entete_papier(d, auj, x0, x1, y, s):
    f = police("mono_moyen", 19 * s)
    espace(d, (x0, y), "TRAKT_ARCHIVE", f, hexa(ENCRE), 2 * s)
    espace(d, (x0, y + 28 * s), f"{MOIS_FR[auj.month - 1].upper()} {auj.year}", f, hexa(ENCRE), 2 * s)
    espace(d, (x1, y), f"{auj.month:02d}", f, hexa(ENCRE), 2 * s, "rs")
    d.line([(x1 - 64 * s, y + 18 * s), (x1, y + 18 * s)], fill=hexa(ENCRE), width=max(1, int(2 * s)))
    espace(d, (x1, y + 48 * s), f"MAJ {datetime.now():%d/%m %H:%M}", police("mono", 17 * s),
           hexa(GRIS_P), 2 * s, "rs")


def compte_mois(mois):
    """« 2 FILMS », « 2 FILMS · 5 ÉP. » ou « 5 ÉP. »."""
    morceaux = []
    if mois["films"] or not mois["episodes"]:
        morceaux.append(f"{mois['films']} FILM{'S' if mois['films'] > 1 else ''}")
    if mois["episodes"]:
        morceaux.append(f"{mois['episodes']} ÉP.")
    return " · ".join(morceaux)


def regrouper_series(liste):
    """Les épisodes d'une même série vus le même jour forment une seule ligne : « THE BEAR · 3 ÉP. »."""
    lignes, index = [], {}
    for v in liste:
        if not v.get("serie"):
            lignes.append(dict(v))
            continue
        cle = (v["jour"], v["serie"])
        if cle in index:
            ligne = lignes[index[cle]]
            ligne["duree"] = (ligne["duree"] or 0) + (v["duree"] or 0)
            ligne["nb"] += 1
        else:
            index[cle] = len(lignes)
            lignes.append(dict(v, nb=1))
    for ligne in lignes:
        if "nb" in ligne:
            ligne["libelle"] = f"{ligne['titre']} · {ligne['nb']} ÉP."
    return lignes


def liste_films(d, liste, x0, x1, y, s, nb_max, taille=22, reste=True):
    f = police("mono", taille * s)
    fg = police("mono_moyen", taille * s)
    pas = taille * 2.1 * s
    couleurs = couleurs_mois(liste)      # mêmes couleurs que dans l'orbe (une par film ou par série)
    liste = regrouper_series(liste)
    if reste and len(liste) > nb_max:
        nb_max -= 1      # la dernière ligne annonce les films cachés
    for v in liste[-nb_max:]:
        espace(d, (x0, y), f"{v['jour']:02d}", f, hexa(GRIS_P), 1 * s)
        cx = x0 + 62 * s
        r = 8 * s
        cy = y - taille * 0.36 * s
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=hexa(couleurs[v["titre"]]))
        duree = duree_txt(v["duree"]).upper() if v["duree"] else ""
        wd = largeur_espace(d, duree, f, 1 * s)
        titre = tronquer(v.get("libelle", v["titre"]).upper(), fg, x1 - (cx + 26 * s) - wd - 40 * s, d)
        espace(d, (cx + 26 * s, y), titre, fg, hexa(ENCRE), 1 * s)
        espace(d, (x1, y), duree, f, hexa(GRIS_P), 1 * s, "rs")
        y += pas
    caches = len(liste) - nb_max
    if reste and caches > 0:
        espace(d, (x0 + 88 * s, y), f"+ {caches} AUTRE{'S' if caches > 1 else ''} PLUS TÔT CE MOIS",
               f, hexa(GRIS_P), 1 * s)
        y += pas
    return y


def pauses(auj, annee):
    """Plus longue suite de jours sans film dans l'année, et la pause en cours."""
    vus_ = {date(auj.year, m, v["jour"]) for m, l in annee["par_mois"].items() for v in l}
    jour, cours, plus_longue = date(auj.year, 1, 1), 0, 0
    while jour <= auj:
        cours = 0 if jour in vus_ else cours + 1
        plus_longue = max(plus_longue, cours)
        jour += timedelta(days=1)
    return plus_longue, cours


def bandeau_annee(img, auj, annee, x0, x1, y0, y1, s, compact=False):
    """Un petit orbe par mois, et sous chacun une frise : un trait par jour."""
    d = ImageDraw.Draw(img)
    f_t = police("mono_moyen", (20 if compact else 19) * s)
    f_g = police("mono", (20 if compact else 19) * s)
    x = espace(d, (x0, y0), f"COLLECTION {auj.year}", f_t, hexa(ENCRE), 2 * s) + 30 * s
    plus_longue, en_cours = pauses(auj, annee)
    for txt in [duree_txt(annee["temps"]).upper() if annee["temps"] else None,
                f"{annee['films']} FILMS",
                None if compact or not annee["episodes"] else f"{annee['episodes']} ÉPISODES",
                f"PAUSE MAX {plus_longue} J" if plus_longue > 1 else None]:
        if txt:
            x = espace(d, (x, y0), txt, f_g, hexa(GRIS_P), 2 * s) + 30 * s

    # Sur iPhone : deux rangées de six mois, pour que chaque jour reste lisible
    rangs = 2 if compact else 1
    par_rang = 12 // rangs
    pas = (x1 - x0) / par_rang
    f_m = police("mono_moyen", (19 if compact else 18) * s)
    f_h = police("mono", 17 * s)
    h_barre = (30 if compact else 44) * s       # hauteur maxi d'une barre de film
    h_off = (9 if compact else 11) * s          # trait d'un jour sans film
    bas_lab = (34 if compact else 58) * s
    ecart_rang = 30 * s if rangs > 1 else 0
    jeu = (12 if compact else 22) * s           # entre l'orbe et sa frise
    h_rang = ((y1 - y0) - 44 * s - ecart_rang * (rangs - 1)) / rangs
    diam = min(pas * (0.42 if compact else 0.62), (h_rang - h_barre - jeu - bas_lab - 8 * s) / 1.135)
    ep_off = max(1, int(1.6 * s))
    for m in range(1, 13):
        rang, col = divmod(m - 1, par_rang)
        cx = x0 + pas * (col + 0.5)
        cy = y0 + 44 * s + rang * (h_rang + ecart_rang) + diam / 2 * 1.09
        base = cy + diam / 2 * 1.18 + jeu + h_barre
        r = diam / 2
        nb_j = monthrange(auj.year, m)[1]
        liste = annee["par_mois"].get(m, [])
        if m <= auj.month:
            o = orbe(liste, nb_j, diam / 0.7)
            img.alpha_composite(o, (int(cx - o.width / 2), int(cy - o.height / 2)))
            if m == auj.month:
                d.ellipse([cx - r * 1.18, cy - r * 1.18, cx + r * 1.18, cy + r * 1.18],
                          outline=hexa(ENCRE), width=max(2, int(2.5 * s)))
        else:
            for k in range(36):
                a = 2 * math.pi * k / 36
                px, py = cx + r * 0.86 * math.cos(a), cy + r * 0.86 * math.sin(a)
                rp = 1.6 * s
                d.ellipse([px - rp, py - rp, px + rp, py + rp], fill=hexa("#B5B1AA"))

        # Frise du mois
        fx0, fx1 = cx - pas * 0.44, cx + pas * 0.44
        pj = (fx1 - fx0) / nb_j
        couleurs = couleurs_mois(liste)
        par_jour = {}
        for v in liste:
            par_jour.setdefault(v["jour"], []).append(v)
        ep = max(2, pj * 0.6)
        for j in range(1, nb_j + 1):
            x = fx0 + (j - 0.5) * pj
            jour = date(auj.year, m, j)
            if j in par_jour:
                films = sorted(par_jour[j], key=lambda v: -v["duree"])
                total = sum(v["duree"] or 90 for v in films)
                h = h_barre * (0.45 + 0.55 * min(1.0, total / 240))
                y = base
                for v in films:
                    part = h * ((v["duree"] or 90) / total)
                    d.rectangle([x - ep / 2, y - part, x + ep / 2, y], fill=hexa(couleurs[v["titre"]]))
                    y -= part
            elif jour < auj:
                d.line([(x, base), (x, base - h_off)], fill=hexa("#6F6B65"), width=ep_off)
            elif jour == auj:
                d.polygon([(x - 6 * s, base + 14 * s), (x + 6 * s, base + 14 * s), (x, base + 4 * s)],
                          fill=hexa(ORANGE))
            else:
                d.line([(x, base), (x, base - 3 * s)], fill=hexa("#C9C5BE"), width=ep_off)
        d.line([(fx0, base + 2 * s), (fx1, base + 2 * s)],
               fill=hexa(ENCRE if m <= auj.month else "#C9C5BE"), width=max(1, int(1.5 * s)))

        couleur = ENCRE if m <= auj.month else "#A9A59E"
        nom = MOIS_COURT[m - 1].rstrip(".")
        yl = base + (36 if compact else 42) * s
        espace(d, (cx, yl), nom, f_m, hexa(couleur), 2 * s, "ms")
        if not compact and m <= auj.month:
            minutes = sum(v["duree"] for v in liste)
            espace(d, (cx, yl + 28 * s), duree_txt(minutes).upper() if minutes else "—", f_h,
                   hexa(GRIS_P), 1 * s, "ms")


def theme_papier(vus, auj, L, H, stats="", annee=None):
    s = H / 1800
    img = fond_papier(L, H, s)
    mois = annee["mois"]
    premier, nb_jours, _ = calendrier(auj)
    marge = L * 0.06

    # Orbe + cadran
    cx, cy, R = L * 0.37, H * 0.45, H * 0.24
    o = orbe(mois["liste"], nb_jours, R * 2 / 0.7)
    img.alpha_composite(o, (int(cx - o.width / 2), int(cy - o.height / 2)))
    d = ImageDraw.Draw(img)
    cadran(d, cx, cy, R * 1.2, vus, auj, nb_jours, s)
    entete_papier(d, auj, marge, L - marge, H * 0.075, s)

    # Colonne de droite : le temps du mois, l'écart, la liste
    x0, x1 = L * 0.64, L - marge
    f_gros = police("serif", 210 * s)
    base = H * 0.33
    d.text((x0 - 6 * s, base), stats or "0 h", font=f_gros, fill=hexa(ENCRE), anchor="ls")
    f_it = police("serif_italique", 46 * s)
    devant = "l'écran" if INCLURE_EPISODES else "des films"
    d.text((x0, base + 62 * s), f"devant {devant} en {MOIS_FR[auj.month - 1]}", font=f_it,
           fill=hexa("#6B6862"), anchor="ls")
    nb = len(mois["liste"])
    f_p = police("mono_moyen", 24 * s)
    ecart = ecart_txt(mois)
    pastille(d, x0, base + 104 * s, compte_mois(mois),
             f"{ecart} VS {MOIS_COURT[auj.month - 2]}" if ecart else None, f_p, s)
    y = base + 270 * s
    d.line([(x0, y - 50 * s), (x1, y - 50 * s)], fill=hexa("#BDB9B2"), width=max(1, int(2 * s)))
    y = liste_films(d, mois["liste"], x0, x1, y, s, 9)
    if mois["serie"] > 1:
        espace(d, (x0, y + 20 * s), f"SÉRIE EN COURS — {mois['serie']} JOURS", police("mono", 20 * s),
               hexa(GRIS_P), 2 * s)

    # L'année en bas
    bandeau_annee(img, auj, annee, marge, L - marge, H * 0.785, H * 0.965, s)
    return img


def iphone_papier(vus, auj, L, H, stats="", annee=None):
    s = L / 1290
    img = fond_papier(L, H, s * 1.1)
    mois = annee["mois"]
    premier, nb_jours, _ = calendrier(auj)
    marge = L * 0.08

    cx, cy, R = L / 2, H * 0.437, L * 0.23
    o = orbe(mois["liste"], nb_jours, R * 2 / 0.7)
    img.alpha_composite(o, (int(cx - o.width / 2), int(cy - o.height / 2)))
    d = ImageDraw.Draw(img)
    cadran(d, cx, cy, R * 1.2, vus, auj, nb_jours, s * 1.15)
    entete_papier(d, auj, marge, L - marge, H * 0.287, s * 1.2)

    base = H * 0.65
    f_gros = police("serif", 200 * s)
    d.text((marge - 6 * s, base), stats or "0 h", font=f_gros, fill=hexa(ENCRE), anchor="ls")
    f_p = police("mono_moyen", 26 * s)
    nb = len(mois["liste"])
    ecart = ecart_txt(mois)
    largeur_p = pastille(d, 0, -1000, compte_mois(mois),
                         f"{ecart}" if ecart else None, f_p, s * 1.1) - 0
    pastille(d, L - marge - largeur_p, base - 70 * s, compte_mois(mois),
             f"{ecart}" if ecart else None, f_p, s * 1.1)
    y = liste_films(d, mois["liste"], marge, L - marge, base + 80 * s, s, 3, taille=27)

    bandeau_annee(img, auj, annee, marge, L - marge, H * 0.765, H * 0.925, s * 1.1, compact=True)
    return img


IPHONE = {"appareil": iphone_appareil, "papier": iphone_papier}
GRAIN = {"appareil": 7, "papier": 12}


THEMES = {"salle": theme_salle, "pellicule": theme_pellicule, "affiche": theme_affiche,
          "appareil": theme_appareil, "papier": theme_papier}


# ---------- Écran et application ----------

def resolution_ecran():
    try:
        sortie = subprocess.run(["system_profiler", "SPDisplaysDataType"],
                                capture_output=True, text=True, timeout=15).stdout
        # Prend l'écran le plus grand (utile avec un moniteur externe 4K/5K)
        tailles = [(int(a), int(b)) for a, b in re.findall(r"Resolution:\s*(\d+)\s*x\s*(\d+)", sortie)]
        if tailles:
            return max(tailles, key=lambda t: t[0] * t[1])
    except Exception:
        pass
    return 2880, 1800


def appliquer_fond(chemin):
    script = f'tell application "System Events" to tell every desktop to set picture to "{chemin}"'
    subprocess.run(["osascript", "-e", script], check=True)


def rendre(nom, vus, auj, L, H, stats, annee, iphone=False, SUR=3):
    """Dessin en 3x puis réduction (bords lisses), puis grain éventuel."""
    if iphone:
        fonction = IPHONE.get(nom, iphone_affiche)
    else:
        fonction = THEMES.get(nom, theme_affiche)
    img = fonction(vus, auj, L * SUR, H * SUR, stats, annee).convert("RGB").resize((L, H), Image.LANCZOS)
    return grain(img, GRAIN.get(nom, 0))


def main_serveur(demo=False):
    """Mode GitHub : génère les deux images dans site/, sans toucher au système."""
    global DOSSIER
    DOSSIER = Path.cwd()
    auj = date.today()
    vus, stats, annee = preparer(charger_annee(auj, demo), auj)
    site = Path("site")
    site.mkdir(exist_ok=True)
    nom = os.environ.get("THEME", "affiche")
    L, H = MAC_TAILLE
    rendre(nom, vus, auj, L, H, stats, annee).save(site / "mac.png")
    W, Hp = IPHONE_TAILLE
    rendre(nom, vus, auj, W, Hp, stats, annee, iphone=True).save(site / "iphone.png")
    # Empreinte de chaque image : les appareils ne téléchargent l'image que si elle a changé
    for nom in ("mac", "iphone"):
        empreinte = hashlib.sha256((site / f"{nom}.png").read_bytes()).hexdigest()
        (site / f"{nom}.txt").write_text(empreinte + "\n")
    (site / "index.html").write_text(
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
        "<title>Fond Trakt</title><body style='background:#000;margin:0;padding:16px;font-family:sans-serif;color:#888'>"
        f"<p>Mis à jour le {datetime.now():%d/%m/%Y à %H:%M}</p>"
        "<img src='iphone.png' style='max-width:100%;height:auto'><br><br>"
        "<img src='mac.png' style='max-width:100%;height:auto'></body>", encoding="utf-8")
    print("Images générées dans site/")


def main():
    demo = "--demo" in sys.argv
    if "--ci" in sys.argv:
        return main_serveur(demo)
    auj = date.today()
    DOSSIER.mkdir(parents=True, exist_ok=True)
    vus, stats, annee = preparer(charger_annee(auj, demo), auj)
    L, H = resolution_ecran() if sys.platform == "darwin" else (2880, 1800)
    # Dessin en 3x puis réduction : bords des ronds et contours parfaitement lisses
    img = rendre(THEME, vus, auj, L, H, stats, annee)

    for ancien in DOSSIER.glob("fond_*.png"):
        ancien.unlink()
    chemin = DOSSIER / f"fond_{datetime.now():%Y%m%d_%H%M%S}.png"
    img.save(chemin)
    print(f"Image enregistrée : {chemin}")
    if sys.platform == "darwin":
        appliquer_fond(str(chemin))
        print("Fond d'écran mis à jour.")

    if VERSION_IPHONE:
        W, Hp = IPHONE_TAILLE
        tel = rendre(THEME, vus, auj, W, Hp, stats, annee, iphone=True)
        icloud = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs"
        dossier_tel = (icloud if icloud.exists() else DOSSIER) / "Fond Trakt"
        dossier_tel.mkdir(parents=True, exist_ok=True)
        tel.save(dossier_tel / "iphone.png")
        print(f"Version iPhone : {dossier_tel / 'iphone.png'}")


if __name__ == "__main__":
    main()
