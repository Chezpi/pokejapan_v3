"""
PokeJapan v3 — Backend con datos reales
Catálogo: TCGdex API (oficial, gratuito)
Precios:  Pokécazilla (scraping, selectores reales)
          SneakerDunk (scraping)
WebSocket: push instantáneo de cambios de precio
"""

import os, re, asyncio, logging, json
from datetime import datetime
from typing import Optional
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor
import requests
from bs4 import BeautifulSoup
import httpx
from bs4 import BeautifulSoup
import re

BASE_URL = "https://www.cardrush-pokemon.jp"

def map_condition(text):
    if "美品" in text:
        return "A"
    elif "良品" in text:
        return "B"
    elif "並品" in text:
        return "C"
    elif "傷" in text:
        return "D"
    return None

def extract_price(text):
    # Extrae números tipo 32,800円
    match = re.search(r'([\d,]+)', text)
    if match:
        return int(match.group(1).replace(",", ""))
    return 0

def extract_psa(text):
    match = re.search(r'PSA\s?(\d+)', text.upper())
    if match:
        return f"PSA {match.group(1)}"
    return None


async def scrape_cardrush(query="リザードン"):
    url = f"{BASE_URL}/product-list?keyword={query}"

    headers = {
        "User-Agent": "Mozilla/5.0"
    }

    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers)

    soup = BeautifulSoup(r.text, "lxml")

    products = []

    items = soup.select(".product_item")

    for i, item in enumerate(items[:30]):

        text = item.get_text(" ", strip=True)

        name = text[:100]

        price = extract_price(text)

        condition = map_condition(text)

        psa = extract_psa(text)

        link_tag = item.select_one("a")
        link = BASE_URL + link_tag["href"] if link_tag else BASE_URL

        products.append({
            "id": i,
            "name": name,
            "name_ja": name,
            "set": "",
            "sname": "",
            "rarity": "",
            "cat": "carta",
            "sources": [
                {
                    "src": "cardrush",
                    "jpy": price,
                    "condition": condition,
                    "grade": psa,
                    "url": link,
                    "imgs": []
                }
            ]
        })

    return products

@app.get("/products")
async def get_products(q: str = "リザードン"):
    products = await scrape_cardrush(q)

    return {
        "products": products,
        "total": len(products)
    }

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

DATABASE_URL = os.environ.get("DATABASE_URL")
COMM       = 0.06
IVA        = 0.21
FRIEND_JPY = 1000
EUR_JPY    = 162.0
FX         = {"EUR": 1.0, "USD": 1.08, "GBP": 0.85, "JPY": EUR_JPY}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "ja-JP,ja;q=0.9",
}

# TCGdex base URL
TCGDEX = "https://api.tcgdex.net/v2"

# Expansiones japonesas modernas a indexar (set IDs de TCGdex)
# Puedes ampliar esta lista con todos los sets que quieras vender
JP_SETS = [
    "sv1",   # Scarlet & Violet base
    "sv2",   # Paldea Evolved
    "sv3",   # Obsidian Flames
    "sv4",   # Paradox Rift
    "sv5",   # Temporal Forces
    "sv6",   # Twilight Masquerade
    "sv7",   # Stellar Crown
    "swsh1", # Sword & Shield base
    "swsh2", # Rebel Clash
    "swsh3", # Darkness Ablaze
]

# ─── WebSocket manager ──────────────────────────────────────────────────────

class WSManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        msg  = json.dumps(data, ensure_ascii=False)
        dead = []
        for ws in self.active:
            try:
                await ws.send_text(msg)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.remove(ws)

manager = WSManager()

# ─── Base de datos ─────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id          SERIAL PRIMARY KEY,
            tcgdex_id   TEXT UNIQUE,         -- e.g. "sv1-001"
            name_ja     TEXT NOT NULL,       -- nombre japonés oficial
            name_en     TEXT DEFAULT '',     -- nombre inglés
            set_id      TEXT,                -- e.g. "sv1"
            set_name    TEXT DEFAULT '',
            number      TEXT DEFAULT '',     -- número en el set
            rarity      TEXT DEFAULT '',
            image_url   TEXT DEFAULT '',
            category    TEXT DEFAULT 'carta' CHECK(category IN ('carta','promo')),
            created_at  TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS prices (
            id          SERIAL PRIMARY KEY,
            card_id     INTEGER REFERENCES cards(id),
            source      TEXT NOT NULL,       -- 'pokecazilla' | 'sneakerdunk'
            price_jpy   INTEGER NOT NULL,
            url         TEXT DEFAULT '',
            scraped_at  TIMESTAMP DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_prices_card ON prices(card_id, scraped_at DESC);
        CREATE INDEX IF NOT EXISTS idx_cards_tcgdex ON cards(tcgdex_id);
    """)
    conn.commit(); cur.close(); conn.close()
    log.info("DB inicializada")

def get_last_price(card_id: int, source: str) -> Optional[int]:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT price_jpy FROM prices
        WHERE card_id = %s AND source = %s
        ORDER BY scraped_at DESC LIMIT 1
    """, (card_id, source))
    row = cur.fetchone()
    cur.close(); conn.close()
    return row["price_jpy"] if row else None

def save_price(card_id: int, source: str, price_jpy: int, url: str = ""):
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO prices (card_id, source, price_jpy, url) VALUES (%s,%s,%s,%s)",
        (card_id, source, price_jpy, url)
    )
    conn.commit(); cur.close(); conn.close()

def upsert_card(tcgdex_id: str, name_ja: str, name_en: str,
                set_id: str, set_name: str, number: str,
                rarity: str, image_url: str, category: str) -> int:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id FROM cards WHERE tcgdex_id = %s", (tcgdex_id,))
    row = cur.fetchone()
    if row:
        cid = row["id"]
        cur.execute("""
            UPDATE cards SET name_ja=%s, name_en=%s, set_name=%s,
            rarity=%s, image_url=%s WHERE id=%s
        """, (name_ja, name_en, set_name, rarity, image_url, cid))
    else:
        cur.execute("""
            INSERT INTO cards (tcgdex_id,name_ja,name_en,set_id,set_name,number,rarity,image_url,category)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (tcgdex_id, name_ja, name_en, set_id, set_name, number, rarity, image_url, category))
        cid = cur.fetchone()["id"]
    conn.commit(); cur.close(); conn.close()
    return cid

def get_all_cards_with_prices() -> list[dict]:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT c.id, c.tcgdex_id, c.name_ja, c.name_en, c.set_name,
               c.number, c.rarity, c.image_url, c.category,
               json_agg(
                 json_build_object(
                   'source',    pr.source,
                   'price_jpy', pr.price_jpy,
                   'url',       pr.url,
                   'scraped_at',pr.scraped_at::text
                 ) ORDER BY pr.price_jpy ASC
               ) FILTER (WHERE pr.source IS NOT NULL) AS prices
        FROM cards c
        LEFT JOIN LATERAL (
            SELECT DISTINCT ON (source) source, price_jpy, url, scraped_at
            FROM prices WHERE card_id = c.id
            ORDER BY source, scraped_at DESC
        ) pr ON true
        GROUP BY c.id
        ORDER BY c.set_id, c.number
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()
    return [dict(r) for r in rows]

# ─── Cálculo de precio final ───────────────────────────────────────────────

def calc_final(jpy: int, cur: str = "EUR") -> float:
    total = (jpy + FRIEND_JPY) * (1 + COMM) * (1 + IVA)
    return round((total / EUR_JPY) * FX.get(cur, 1.0), 2)

def parse_jpy(text: str) -> Optional[int]:
    nums = re.sub(r"[^\d]", "", text)
    if nums:
        v = int(nums)
        if 100 <= v <= 5_000_000:
            return v
    return None

# ─── TCGdex: importar catálogo oficial ────────────────────────────────────

async def fetch_tcgdex_set(client: httpx.AsyncClient, set_id: str) -> list[dict]:
    """Descarga todas las cartas de una expansión desde TCGdex."""
    cards = []
    try:
        # 1. Info del set
        r = await client.get(f"{TCGDEX}/ja/sets/{set_id}", timeout=15)
        if r.status_code != 200:
            # Intentar en inglés si no existe en japonés
            r = await client.get(f"{TCGDEX}/en/sets/{set_id}", timeout=15)
        if r.status_code != 200:
            log.warning(f"TCGdex set {set_id} no encontrado")
            return []

        set_data = r.json()
        set_name = set_data.get("name", set_id)
        set_cards = set_data.get("cards", [])

        log.info(f"TCGdex {set_id} ({set_name}): {len(set_cards)} cartas")

        # 2. Detalle de cada carta (en lotes para no sobrecargar)
        for card_brief in set_cards[:200]:  # máx 200 por set
            local_id = card_brief.get("localId", "")
            tcgdex_id = f"{set_id}-{local_id}"

            try:
                rc = await client.get(f"{TCGDEX}/ja/cards/{tcgdex_id}", timeout=10)
                if rc.status_code == 200:
                    c = rc.json()
                elif rc.status_code == 404:
                    rc = await client.get(f"{TCGDEX}/en/cards/{tcgdex_id}", timeout=10)
                    c = rc.json() if rc.status_code == 200 else {}
                else:
                    c = {}

                if not c:
                    continue

                name_ja  = c.get("name", card_brief.get("name", ""))
                name_en  = ""
                # Si vino en japonés, buscar traducción inglés
                if "ja" not in tcgdex_id:
                    name_en = name_ja
                    name_ja = c.get("name", name_ja)

                rarity    = c.get("rarity", "")
                number    = c.get("localId", local_id)
                image_url = c.get("image", "") + "/high.webp" if c.get("image") else ""
                category  = "promo" if "promo" in rarity.lower() or "PR" in rarity else "carta"

                cards.append({
                    "tcgdex_id": tcgdex_id,
                    "name_ja":   name_ja,
                    "name_en":   name_en,
                    "set_id":    set_id,
                    "set_name":  set_name,
                    "number":    number,
                    "rarity":    rarity,
                    "image_url": image_url,
                    "category":  category,
                })

                await asyncio.sleep(0.1)  # respetar rate limit TCGdex

            except Exception as e:
                log.warning(f"TCGdex carta {tcgdex_id}: {e}")
                continue

    except Exception as e:
        log.error(f"TCGdex set {set_id}: {e}")

    return cards


async def import_tcgdex_catalog(client: httpx.AsyncClient):
    """Importa el catálogo completo de TCGdex a la DB."""
    log.info("Importando catálogo TCGdex...")
    total = 0
    for set_id in JP_SETS:
        cards = await fetch_tcgdex_set(client, set_id)
        for c in cards:
            upsert_card(**c)
            total += 1
        await asyncio.sleep(1)
    log.info(f"Catálogo importado: {total} cartas")


# ─── Pokécazilla scraper ───────────────────────────────────────────────────
#
# Estructura HTML real observada:
#   .product-card  → tarjeta de producto
#   h3 a           → nombre de la carta
#   .lowest-price a → precio mínimo (enlace a tienda)
#   img            → imagen
#   .product-number → número de carta y rareza
#
# URLs por expansión:
#   /pokemon/products/list?category_id=15&pack_id=XXXX  (Scarlet & Violet)
#   /pokemon/products/list?category_id=14&pack_id=XXXX  (Sword & Shield)
#
# Pokécazilla tiene su propio ID de pack que NO coincide con TCGdex set_id.
# El matching se hace por nombre de carta (normalizado).

# Mapeo TCGdex set_id → Pokécazilla pack_id
# Hay que completar esto manualmente o scrapear la lista de sets de Pokécazilla
POKECAZILLA_PACKS = {
    "sv1":   {"category_id": 15, "pack_id": 3400},
    "sv2":   {"category_id": 15, "pack_id": 3450},
    "sv3":   {"category_id": 15, "pack_id": 3500},
    "sv4":   {"category_id": 15, "pack_id": 3550},
    "sv5":   {"category_id": 15, "pack_id": 3600},
    "swsh1": {"category_id": 14, "pack_id": 2100},
    "swsh3": {"category_id": 14, "pack_id": 2200},
}

async def scrape_pokecazilla_pack(client: httpx.AsyncClient,
                                   category_id: int, pack_id: int) -> list[dict]:
    """Scraping de un set completo de Pokécazilla."""
    results = []
    page = 1

    while True:
        url = (f"https://pokecazilla.com/pokemon/products/list"
               f"?category_id={category_id}&pack_id={pack_id}&page={page}"
               f"&disp_number=100&order=price_asc")
        try:
            r = await client.get(url, timeout=20)
            soup = BeautifulSoup(r.text, "html.parser")

            # Selector real: cada carta está en un bloque con imagen, nombre y precio
            items = soup.select("li.item, div.item-list li, .product-item, article")

            if not items:
                # Intentar selectores alternativos
                items = soup.select("[class*='item']")

            if not items:
                break

            for item in items:
                try:
                    # Nombre: h3 o h2 dentro del item
                    name_el = item.select_one("h3 a, h2 a, .product-name a, .item-name a")
                    if not name_el:
                        name_el = item.select_one("h3, h2, .product-name")

                    # Precio mínimo
                    price_el = item.select_one(
                        ".lowest-price a, .min-price, [class*='lowest'], [class*='price'] a, [class*='price']"
                    )

                    # Imagen
                    img_el  = item.select_one("img")
                    link_el = item.select_one("a[href*='/products/detail']")

                    if not name_el:
                        continue

                    name = name_el.get_text(strip=True)
                    # Limpiar rareza del nombre: "メガゲッコウガex(SAR)" → "メガゲッコウガex"
                    name_clean = re.sub(r'\([A-Za-z★◆●]+\)$', '', name).strip()

                    price_text = price_el.get_text(strip=True) if price_el else ""
                    price_jpy  = parse_jpy(price_text)

                    if not price_jpy:
                        continue

                    detail_url = ""
                    if link_el:
                        href = link_el.get("href", "")
                        detail_url = href if href.startswith("http") else f"https://pokecazilla.com{href}"

                    img_url = img_el.get("src", "") if img_el else ""

                    results.append({
                        "name_raw":  name,
                        "name_clean": name_clean,
                        "source":    "pokecazilla",
                        "price_jpy": price_jpy,
                        "url":       detail_url,
                        "image_url": img_url,
                    })

                except Exception as e:
                    log.warning(f"Pokécazilla item parse error: {e}")
                    continue

            # ¿Hay página siguiente?
            next_btn = soup.select_one("a[rel='next'], .pagination .next, [class*='next']:not([disabled])")
            if not next_btn or page >= 20:
                break
            page += 1
            await asyncio.sleep(2)

        except Exception as e:
            log.error(f"Pokécazilla pack {pack_id} pág {page}: {e}")
            break

    log.info(f"Pokécazilla pack {pack_id}: {len(results)} productos")
    return results


async def match_and_save_pokecazilla(results: list[dict]):
    """
    Empareja los resultados de Pokécazilla con las cartas de la DB
    usando el nombre limpio. Guarda precio si hay cambio.
    """
    conn = get_conn(); cur = conn.cursor()

    for item in results:
        name_clean = item["name_clean"].lower().strip()

        # Buscar carta por nombre japonés (coincidencia exacta o parcial)
        cur.execute("""
            SELECT id, name_ja FROM cards
            WHERE LOWER(name_ja) = %s
               OR LOWER(name_ja) LIKE %s
            LIMIT 1
        """, (name_clean, f"%{name_clean}%"))

        row = cur.fetchone()
        if not row:
            # No encontrada → crear entrada temporal sin tcgdex_id
            cur.execute("""
                INSERT INTO cards (tcgdex_id, name_ja, category, image_url)
                VALUES (%s, %s, 'carta', %s)
                ON CONFLICT (tcgdex_id) DO NOTHING
                RETURNING id
            """, (f"pokecazilla-{name_clean[:30]}", item["name_clean"], item.get("image_url", "")))
            new_row = cur.fetchone()
            if not new_row:
                continue
            card_id = new_row["id"]
        else:
            card_id = row["id"]

        # Actualizar imagen si no tiene
        if item.get("image_url"):
            cur.execute("UPDATE cards SET image_url=%s WHERE id=%s AND image_url=''",
                       (item["image_url"], card_id))

        conn.commit()

        old_price = get_last_price(card_id, "pokecazilla")
        if old_price != item["price_jpy"]:
            save_price(card_id, "pokecazilla", item["price_jpy"], item["url"])

            # Push WebSocket si hay cambio
            direction = "down" if (old_price and item["price_jpy"] < old_price) else "up"
            asyncio.create_task(manager.broadcast({
                "type":       "price_update",
                "card_id":    card_id,
                "name":       item["name_clean"],
                "source":     "pokecazilla",
                "old_jpy":    old_price,
                "new_jpy":    item["price_jpy"],
                "price_eur":  calc_final(item["price_jpy"]),
                "direction":  direction,
                "timestamp":  datetime.now().isoformat(),
            }))

    cur.close(); conn.close()


# ─── SneakerDunk scraper ───────────────────────────────────────────────────

async def scrape_sneakerdunk(client: httpx.AsyncClient) -> list[dict]:
    """
    SneakerDunk sección Pokémon.
    URL: https://snkrdunk.com/en/pokemon/cards
    Estructura real a verificar — selectores pueden variar.
    """
    results = []
    pages   = [
        "https://snkrdunk.com/en/pokemon/cards",
        "https://snkrdunk.com/en/pokemon/cards?page=2",
    ]

    for url in pages:
        try:
            r    = await client.get(url, timeout=20)
            soup = BeautifulSoup(r.text, "html.parser")

            # SneakerDunk usa React/Next.js — los datos pueden estar en __NEXT_DATA__
            next_data = soup.find("script", id="__NEXT_DATA__")
            if next_data:
                data = json.loads(next_data.string)
                # Navegar por el JSON de Next.js para encontrar productos
                try:
                    products = (data.get("props", {})
                                   .get("pageProps", {})
                                   .get("products", []))
                    for p in products:
                        name      = p.get("name", p.get("title", ""))
                        price_jpy = p.get("lowestPrice", p.get("price", 0))
                        img       = p.get("imageUrl", p.get("image", ""))
                        link      = p.get("url", url)
                        if name and price_jpy:
                            results.append({
                                "name_clean": name,
                                "source":     "sneakerdunk",
                                "price_jpy":  int(price_jpy),
                                "url":        link,
                                "image_url":  img,
                            })
                except Exception:
                    pass

            if not results:
                # Fallback: scraping HTML clásico
                for card in soup.select("[class*='ProductCard'], [class*='product-card'], .item"):
                    name_el  = card.select_one("[class*='name'], h3, h2")
                    price_el = card.select_one("[class*='price'], [class*='Price']")
                    img_el   = card.select_one("img")
                    link_el  = card.select_one("a")

                    if not name_el or not price_el:
                        continue
                    jpy = parse_jpy(price_el.get_text())
                    if not jpy:
                        continue
                    results.append({
                        "name_clean": name_el.get_text(strip=True),
                        "source":     "sneakerdunk",
                        "price_jpy":  jpy,
                        "url":        link_el.get("href", url) if link_el else url,
                        "image_url":  img_el.get("src", "") if img_el else "",
                    })

            await asyncio.sleep(3)

        except Exception as e:
            log.error(f"SneakerDunk {url}: {e}")

    log.info(f"SneakerDunk: {len(results)} productos")
    return results


async def match_and_save_sneakerdunk(results: list[dict]):
    """Mismo proceso de matching que Pokécazilla."""
    conn = get_conn(); cur = conn.cursor()

    for item in results:
        name_lower = item["name_clean"].lower().strip()

        cur.execute("""
            SELECT id FROM cards
            WHERE LOWER(name_ja) = %s OR LOWER(name_en) = %s
               OR LOWER(name_ja) LIKE %s OR LOWER(name_en) LIKE %s
            LIMIT 1
        """, (name_lower, name_lower, f"%{name_lower}%", f"%{name_lower}%"))

        row = cur.fetchone()
        if not row:
            continue  # SneakerDunk: solo precios de cartas ya en catálogo

        card_id   = row["id"]
        old_price = get_last_price(card_id, "sneakerdunk")

        if old_price != item["price_jpy"]:
            save_price(card_id, "sneakerdunk", item["price_jpy"], item["url"])
            direction = "down" if (old_price and item["price_jpy"] < old_price) else "up"
            asyncio.create_task(manager.broadcast({
                "type":      "price_update",
                "card_id":   card_id,
                "source":    "sneakerdunk",
                "old_jpy":   old_price,
                "new_jpy":   item["price_jpy"],
                "price_eur": calc_final(item["price_jpy"]),
                "direction": direction,
                "timestamp": datetime.now().isoformat(),
            }))

    conn.commit(); cur.close(); conn.close()


# ─── Loop principal ────────────────────────────────────────────────────────

INTERVALS = {"pokecazilla": 10 * 60, "sneakerdunk": 15 * 60, "catalog": 24 * 3600}
last_run  = {"pokecazilla": 0, "sneakerdunk": 0, "catalog": 0}
import time

async def main_loop():
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:

        # Importar catálogo TCGdex al arrancar si la DB está vacía
        conn = get_conn(); cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as n FROM cards")
        card_count = cur.fetchone()["n"]
        cur.close(); conn.close()

        if card_count == 0:
            await import_tcgdex_catalog(client)
            last_run["catalog"] = time.time()

        while True:
            now = time.time()

            # Re-sincronizar catálogo TCGdex cada 24h
            if now - last_run["catalog"] >= INTERVALS["catalog"]:
                asyncio.create_task(import_tcgdex_catalog(client))
                last_run["catalog"] = now

            # Pokécazilla cada 10 min
            if now - last_run["pokecazilla"] >= INTERVALS["pokecazilla"]:
                try:
                    all_items = []
                    for set_id, pack in POKECAZILLA_PACKS.items():
                        items = await scrape_pokecazilla_pack(
                            client, pack["category_id"], pack["pack_id"]
                        )
                        all_items.extend(items)
                        await asyncio.sleep(2)
                    await match_and_save_pokecazilla(all_items)
                    last_run["pokecazilla"] = time.time()
                except Exception as e:
                    log.error(f"Loop Pokécazilla: {e}")

            # SneakerDunk cada 15 min
            if now - last_run["sneakerdunk"] >= INTERVALS["sneakerdunk"]:
                try:
                    items = await scrape_sneakerdunk(client)
                    await match_and_save_sneakerdunk(items)
                    last_run["sneakerdunk"] = time.time()
                except Exception as e:
                    log.error(f"Loop SneakerDunk: {e}")

            await asyncio.sleep(60)


# ─── FastAPI ────────────────────────────────────────────────────────────────

app = FastAPI(title="PokeJapan API v3")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

@app.on_event("startup")
async def startup():
    init_db()
    asyncio.create_task(main_loop())
    log.info("PokeJapan v3 arrancado")

@app.get("/products")
def get_products(
    category: str = Query(None),
    search:   str = Query(None),
    sort:     str = Query("popular"),
    set_id:   str = Query(None),
):
    rows = get_all_cards_with_prices()
    out  = []
    for row in rows:
        prices = row.get("prices") or []
        if not prices:
            continue
        if category and row["category"] != category:
            continue
        if set_id and row.get("set_id") != set_id:
            continue
        if search:
            q = search.lower()
            if q not in row["name_ja"].lower() and q not in row["name_en"].lower():
                continue

        prices_sorted = sorted(prices, key=lambda x: x["price_jpy"])
        cheapest = prices_sorted[0]
        second   = prices_sorted[1] if len(prices_sorted) > 1 else None

        out.append({
            "id":        row["id"],
            "tcgdex_id": row["tcgdex_id"],
            "name_ja":   row["name_ja"],
            "name_en":   row["name_en"],
            "set_name":  row["set_name"],
            "number":    row["number"],
            "rarity":    row["rarity"],
            "image_url": row["image_url"],
            "category":  row["category"],
            "cheapest":  {**cheapest,  "price_eur": calc_final(cheapest["price_jpy"])},
            "second":    {**second,    "price_eur": calc_final(second["price_jpy"])} if second else None,
        })

    if sort == "asc":
        out.sort(key=lambda x: x["cheapest"]["price_jpy"])
    elif sort == "desc":
        out.sort(key=lambda x: x["cheapest"]["price_jpy"], reverse=True)

    return {"products": out, "total": len(out)}

@app.get("/sets")
def get_sets():
    """Lista de sets disponibles en la DB."""
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT DISTINCT set_id, set_name FROM cards ORDER BY set_id")
    rows = cur.fetchall()
    cur.close(); conn.close()
    return {"sets": [dict(r) for r in rows]}

@app.get("/health")
def health():
    return {"status": "ok", "ws_connections": len(manager.active)}

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        # Enviar catálogo completo al conectar
        rows = get_all_cards_with_prices()
        products = []
        for row in rows:
            prices = row.get("prices") or []
            if not prices:
                continue
            prices_sorted = sorted(prices, key=lambda x: x["price_jpy"])
            products.append({
                "id":        row["id"],
                "name_ja":   row["name_ja"],
                "name_en":   row["name_en"],
                "set_name":  row["set_name"],
                "rarity":    row["rarity"],
                "image_url": row["image_url"],
                "category":  row["category"],
                "cheapest":  {**prices_sorted[0],  "price_eur": calc_final(prices_sorted[0]["price_jpy"])},
                "second":    {**prices_sorted[1], "price_eur": calc_final(prices_sorted[1]["price_jpy"])} if len(prices_sorted) > 1 else None,
            })

        await ws.send_text(json.dumps({
            "type":     "full_catalog",
            "products": products,
            "total":    len(products),
            "timestamp": datetime.now().isoformat(),
        }, ensure_ascii=False))

        while True:
            await ws.receive_text()

    except WebSocketDisconnect:
        manager.disconnect(ws)
