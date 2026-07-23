#!/usr/bin/env python3
"""
Bot Wallapop -> Telegram, versión para GitHub Actions.

Diferencia con la versión de escritorio: NO corre en bucle infinito.
Hace una sola pasada y termina. Es GitHub quien lo lanza cada X minutos.

El token y el chat_id NO van escritos aquí: se leen de los "Secrets"
de GitHub (variables de entorno), que es la forma segura de guardarlos.
"""

import os
import json
import time
import html
import requests
from pathlib import Path

# ======================= CONFIGURACIÓN =======================

KEYWORDS = [
    "televisor de tubo",
    "tv de tubo",
    "television tubo",
    "televisor crt",
    "tv crt",
    "trinitron",
    "tubo catodico",
]

# Centro de búsqueda: Valencia capital
LATITUDE = 39.4699
LONGITUDE = -0.3763
MAX_DISTANCE_KM = 60       # None = sin filtro de distancia

MIN_PRICE = None           # p.ej. 0
MAX_PRICE = None           # p.ej. 150

SEEN_FILE = Path("wallapop_vistos.json")

# Cuántos IDs guardamos como máximo (para que el fichero no crezca sin fin)
MAX_SEEN = 3000

# =============================================================

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

WALLAPOP_ENDPOINT = "https://api.wallapop.com/api/v3/search"

HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/125.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9",
    "X-DeviceOS": "0",
}


def load_seen() -> list:
    if SEEN_FILE.exists():
        try:
            data = json.loads(SEEN_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
    return []


def save_seen(seen_list: list) -> None:
    # Nos quedamos con los más recientes
    recortada = seen_list[-MAX_SEEN:]
    SEEN_FILE.write_text(json.dumps(recortada), encoding="utf-8")


def fetch_listings(keyword: str) -> list:
    params = {
        "source": "search_box",
        "keywords": keyword,
        "latitude": LATITUDE,
        "longitude": LONGITUDE,
        "order_by": "closest",
    }
    if MIN_PRICE is not None:
        params["min_sale_price"] = MIN_PRICE
    if MAX_PRICE is not None:
        params["max_sale_price"] = MAX_PRICE

    try:
        r = requests.get(WALLAPOP_ENDPOINT, params=params,
                         headers=HEADERS, timeout=20)
    except Exception as e:
        print(f"  Error de red al buscar '{keyword}':", e)
        return []

    if r.status_code != 200:
        print(f"  Wallapop devolvió {r.status_code} para '{keyword}'. "
              f"Detalle: {r.text[:300]}")
        return []

    try:
        data = r.json()
    except Exception:
        print("  Respuesta no era JSON.")
        return []

    return extract_items(data)


def extract_items(data: dict) -> list:
    if not isinstance(data, dict):
        return []
    try:
        items = data["data"]["section"]["payload"]["items"]
        if isinstance(items, list) and items:
            return items
    except (KeyError, TypeError):
        pass
    for key in ("search_objects", "items"):
        val = data.get(key)
        if isinstance(val, list) and val:
            return val
    return []


def normalize(item: dict):
    if not isinstance(item, dict):
        return None
    if "content" in item and isinstance(item["content"], dict):
        item = item["content"]

    item_id = item.get("id") or item.get("item_id")
    if not item_id:
        return None

    title = item.get("title") or item.get("name") or "(sin título)"

    price = item.get("price")
    if isinstance(price, dict):
        amount = price.get("amount")
        currency = price.get("currency", "EUR")
    else:
        amount = price or item.get("sale_price")
        currency = item.get("currency", "EUR")

    slug = item.get("web_slug") or item.get("slug")
    url = (f"https://es.wallapop.com/item/{slug}" if slug
           else f"https://es.wallapop.com/item/{item_id}")

    dist_km = None
    raw_dist = item.get("distance")
    if isinstance(raw_dist, (int, float)):
        dist_km = raw_dist / 1000 if raw_dist > 1000 else raw_dist

    return {
        "id": str(item_id),
        "title": str(title),
        "amount": amount,
        "currency": currency,
        "url": url,
        "dist_km": dist_km,
    }


def send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    try:
        r = requests.post(url, data=payload, timeout=20)
        if r.status_code != 200:
            print("  Telegram devolvió", r.status_code, r.text[:200])
    except Exception as e:
        print("  Error enviando a Telegram:", e)


def format_msg(it: dict) -> str:
    precio = (f"{it['amount']} {it['currency']}"
              if it["amount"] is not None else "sin precio")
    linea_dist = f"\n📍 a {it['dist_km']:.0f} km" if it["dist_km"] else ""
    return (f"🖥️ <b>{html.escape(it['title'])}</b>\n"
            f"💶 {html.escape(precio)}{linea_dist}\n"
            f"🔗 {it['url']}")


def main():
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️  Faltan los secretos TELEGRAM_TOKEN / TELEGRAM_CHAT_ID.")
        raise SystemExit(1)

    seen_list = load_seen()
    seen = set(seen_list)
    primera_vez = not SEEN_FILE.exists()

    encontrados = {}
    for kw in KEYWORDS:
        for raw in fetch_listings(kw):
            n = normalize(raw)
            if not n or n["id"] in encontrados:
                continue
            if (MAX_DISTANCE_KM is not None and n["dist_km"] is not None
                    and n["dist_km"] > MAX_DISTANCE_KM):
                continue
            encontrados[n["id"]] = n
        time.sleep(1)

    nuevos = 0
    for item_id, it in encontrados.items():
        if item_id in seen:
            continue
        seen.add(item_id)
        seen_list.append(item_id)
        if primera_vez:
            continue          # 1ª ejecución: solo memoriza, no avisa
        send_telegram(format_msg(it))
        nuevos += 1
        time.sleep(1)

    save_seen(seen_list)

    if primera_vez:
        print(f"1ª ejecución: memorizados {len(encontrados)} anuncios "
              f"existentes (sin avisar).")
    else:
        print(f"Revisado: {len(encontrados)} anuncios vistos, "
              f"{nuevos} nuevo(s) notificado(s).")


if __name__ == "__main__":
    main()
