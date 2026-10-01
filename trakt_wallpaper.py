#!/usr/bin/env python3
"""
Fond d'écran Trakt : un repère par jour du mois, allumé si tu as vu un film ce jour-là.
Trois thèmes : "salle" (sièges de cinéma), "pellicule" (planche contact), "affiche".
Usage : python trakt_wallpaper.py            -> génère l'image et l'applique (Mac)
        python trakt_wallpaper.py --demo     -> données factices, sans Trakt
        python trakt_wallpaper.py --ci       -> mode serveur (GitHub) : images dans le dossier site/
"""

import json
import math
import os
import random
import re
import subprocess
import sys
import urllib.request
from calendar import monthrange
from datetime import date, datetime, timezone
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

# ============ CONFIGURATION ============
TRAKT_CLIENT_ID = "COLLE_TON_CLIENT_ID_ICI"
TRAKT_USERNAME = "ton_pseudo_trakt"
INCLURE_EPISODES = False   # True = les épisodes de séries comptent aussi
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


THEMES = {"salle": theme_salle, "pellicule": theme_pellicule, "affiche": theme_affiche}


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


def main_serveur(demo=False):
    """Mode GitHub : génère les deux images dans site/, sans toucher au système."""
    global DOSSIER
    DOSSIER = Path.cwd()
    auj = date.today()
    vus, stats, annee = preparer(charger_annee(auj, demo), auj)
    site = Path("site")
    site.mkdir(exist_ok=True)
    SUR = 3
    theme = THEMES.get(os.environ.get("THEME", "affiche"), theme_affiche)
    L, H = MAC_TAILLE
    theme(vus, auj, L * SUR, H * SUR, stats, annee).convert("RGB").resize((L, H), Image.LANCZOS).save(site / "mac.png")
    W, Hp = IPHONE_TAILLE
    iphone_affiche(vus, auj, W * SUR, Hp * SUR, stats, annee).convert("RGB").resize((W, Hp), Image.LANCZOS).save(site / "iphone.png")
    # Empreinte de chaque image : les appareils ne téléchargent l'image que si elle a changé
    import hashlib
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
    SUR = 3
    grand = THEMES.get(THEME, theme_affiche)(vus, auj, L * SUR, H * SUR, stats, annee)
    img = grand.convert("RGB").resize((L, H), Image.LANCZOS)

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
        tel = iphone_affiche(vus, auj, W * SUR, Hp * SUR, stats, annee)
        tel = tel.convert("RGB").resize((W, Hp), Image.LANCZOS)
        icloud = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs"
        dossier_tel = (icloud if icloud.exists() else DOSSIER) / "Fond Trakt"
        dossier_tel.mkdir(parents=True, exist_ok=True)
        tel.save(dossier_tel / "iphone.png")
        print(f"Version iPhone : {dossier_tel / 'iphone.png'}")


if __name__ == "__main__":
    main()
