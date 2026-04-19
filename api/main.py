"""
PokeJapan — API + Scraper en tiempo real
Datos reales de Pokécazilla (selectores verificados abril 2025)
WebSocket push instantáneo de cambios de precio
"""

import os, re, asyncio, logging, json, time
from datetime import datetime
from typing import Optional
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from psycopg2.extras import RealDictCursor

app = FastAPI()

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
COMM        = 0.05    # 5% tu comisión
IVA_DEFAULT = 0.21    # fallback España — el frontend aplica el del país del cliente
FRIEND_JPY  = 1000    # comisión fija amigo Japón
EUR_JPY     = 162.0

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# ─── Categorías de Pokécazilla ──────────────────────────────────────────────
# category_id 15 = Scarlet & Violet (las más populares y actuales)
# category_id 14 = Sword & Shield
# Scrapear sin pack_id = toda la serie, ordenada por precio descendente
POKECAZILLA_URLS = [
    # Scarlet & Violet — ordenadas por precio desc para pilllar las más caras primero
    "https://pokecazilla.com/pokemon/products/list?category_id=15&order=price_desc",
    # Sword & Shield
    "https://pokecazilla.com/pokemon/products/list?category_id=14&order=price_desc",
]

# ─── WebSocket manager ─────────────────────────────────────────────────────

class WSManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)
        log.info(f"WS conectado. Total: {len(self.active)}")

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)
        log.info(f"WS desconectado. Total: {len(self.active)}")

    async def broadcast(self, data: dict):
        if not self.active:
            return
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

# ─── Base de datos ──────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def init_db():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        cur = conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            name TEXT
        );
        """)

        conn.commit()
        cur.close()
        conn.close()

        print("✅ DB OK")

    except Exception as e:
        print("❌ ERROR DB:", e)

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

def upsert_card(pokecazilla_id: str, name_ja: str, set_code: str,
                set_name: str, number: str, rarity: str,
                image_url: str, detail_url: str, category: str) -> int:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT id FROM cards WHERE pokecazilla_id = %s", (pokecazilla_id,))
    row = cur.fetchone()
    if row:
        cid = row["id"]
        cur.execute("""
            UPDATE cards SET name_ja=%s, set_code=%s, set_name=%s, number=%s,
                rarity=%s, image_url=%s, detail_url=%s, category=%s,
                updated_at=NOW()
            WHERE id=%s
        """, (name_ja, set_code, set_name, number, rarity,
              image_url, detail_url, category, cid))
    else:
        cur.execute("""
            INSERT INTO cards
                (pokecazilla_id,name_ja,set_code,set_name,number,rarity,
                 image_url,detail_url,category)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (pokecazilla_id, name_ja, set_code, set_name, number, rarity,
              image_url, detail_url, category))
        cid = cur.fetchone()["id"]
    conn.commit(); cur.close(); conn.close()
    return cid

def save_price(card_id: int, source: str, price_jpy: int, url: str = ""):
    conn = get_conn(); cur = conn.cursor()
    cur.execute(
        "INSERT INTO prices (card_id, source, price_jpy, url) VALUES (%s,%s,%s,%s)",
        (card_id, source, price_jpy, url)
    )
    conn.commit(); cur.close(); conn.close()

def get_all_products() -> list[dict]:
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT
            c.id, c.pokecazilla_id, c.name_ja, c.name_en,
            c.set_code, c.set_name, c.number, c.rarity,
            c.image_url, c.detail_url, c.category,
            COALESCE(
                json_agg(
                    json_build_object(
                        'source',    pr.source,
                        'price_jpy', pr.price_jpy,
                        'url',       pr.url,
                        'scraped_at',pr.scraped_at::text
                    ) ORDER BY pr.price_jpy ASC
                ) FILTER (WHERE pr.source IS NOT NULL),
                '[]'::json
            ) AS prices
        FROM cards c
        LEFT JOIN LATERAL (
            SELECT DISTINCT ON (source)
                source, price_jpy, url, scraped_at
            FROM prices
            WHERE card_id = c.id
            ORDER BY source, scraped_at DESC
        ) pr ON true
        GROUP BY c.id
        ORDER BY
            CASE c.rarity
                WHEN 'MUR' THEN 1 WHEN 'BWR' THEN 2 WHEN 'ACE' THEN 3
                WHEN 'UR'  THEN 4 WHEN 'HR'  THEN 5 WHEN 'SSR' THEN 6
                WHEN 'SAR' THEN 7 WHEN 'CSR' THEN 8 WHEN 'SR'  THEN 9
                WHEN 'CHR' THEN 10 WHEN 'AR' THEN 11 WHEN 'RR' THEN 12
                ELSE 20
            END,
            (
                SELECT price_jpy FROM prices p2
                WHERE p2.card_id = c.id
                ORDER BY scraped_at DESC LIMIT 1
            ) DESC NULLS LAST
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()
    return [dict(r) for r in rows]

# ─── Utilidades ─────────────────────────────────────────────────────────────

def parse_jpy(text: str) -> Optional[int]:
    """Extrae precio en yenes. Acepta ￥92,800 o ¥92,800 o 92800"""
    nums = re.sub(r"[^\d]", "", text)
    if nums:
        v = int(nums)
        if 100 <= v <= 10_000_000:
            return v
    return None

def parse_set_rarity(text: str) -> tuple[str, str, str]:
    """
    Parsea '[sv2a] 201/165 (SAR)' → ('sv2a', '201/165', 'SAR')
    Parsea '[] 288/SV-P ()' → ('promo', '288/SV-P', 'PR')
    """
    set_code = ""
    number   = ""
    rarity   = ""

    m = re.search(r'\[([^\]]+)\]', text)
    if m and m.group(1).strip():
        set_code = m.group(1).strip()

    m = re.search(r'\]\s+([\d/A-Za-z\-]+)', text)
    if m:
        number = m.group(1).strip()

    m = re.search(r'\(([A-Za-z★◆●☆]+)\)', text)
    if m:
        rarity = m.group(1).strip()

    # Si el set está vacío y hay SV-P en el número → es promo
    if not set_code and "SV-P" in number:
        set_code = "promo"
        if not rarity:
            rarity = "PR"

    return set_code, number, rarity

def guess_category(name: str, rarity: str) -> str:
    n = name.lower()
    r = rarity.upper()
    if any(w in n for w in ["ボックス", "box", "パック", "デッキ", "スターター"]):
        return "caja"
    if "プロモ" in name or "SV-P" in name or r == "PR":
        return "promo"
    return "carta"

def calc_final_eur(jpy: int) -> float:
    """Precio final en EUR con comisión 5% + amigo ¥1000 + IVA España 21%"""
    total = (jpy + FRIEND_JPY) * (1 + COMM) * (1 + IVA_DEFAULT)
    return round(total / EUR_JPY, 2)

# ─── Scraper Pokécazilla ────────────────────────────────────────────────────

# ─── Guardar y notificar cambios ────────────────────────────────────────────

async def process_results(results: list[dict]):
    """Guarda en DB y emite WS si hay cambio de precio."""
    updated = 0
    new     = 0
    
    for item in results:
        try:
            cid = upsert_card(
                pokecazilla_id = item["pokecazilla_id"],
                name_ja        = item["name_ja"],
                set_code       = item["set_code"],
                set_name       = item["set_name"],
                number         = item["number"],
                rarity         = item["rarity"],
                image_url      = item["image_url"],
                detail_url     = item["detail_url"],
                category       = item["category"],
            )
            
            old_price = get_last_price(cid, "pokecazilla")
            new_price = item["price_jpy"]
            
            if old_price is None:
                save_price(cid, "pokecazilla", new_price, item["price_url"])
                new += 1
            elif old_price != new_price:
                save_price(cid, "pokecazilla", new_price, item["price_url"])
                updated += 1
                direction = "down" if new_price < old_price else "up"
                
                asyncio.create_task(manager.broadcast({
                    "type":       "price_update",
                    "card_id":    cid,
                    "name":       item["name_ja"],
                    "rarity":     item["rarity"],
                    "set_name":   item["set_name"],
                    "source":     "pokecazilla",
                    "old_jpy":    old_price,
                    "new_jpy":    new_price,
                    "price_eur":  calc_final_eur(new_price),
                    "direction":  direction,
                    "timestamp":  datetime.now().isoformat(),
                }))
                
                log.info(f"  Precio cambiado: {item['name_ja']} "
                         f"¥{old_price:,} → ¥{new_price:,} ({direction})")
        
        except Exception as e:
            log.error(f"Error procesando {item.get('name_ja','?')}: {e}")
    
    log.info(f"Procesado: {new} nuevas, {updated} actualizadas")

# ─── Loop principal ──────────────────────────────────────────────────────────

SCRAPE_INTERVAL = 10 * 60  # cada 10 minutos
last_scrape = 0.0



# ─── FastAPI ─────────────────────────────────────────────────────────────────

app = FastAPI(title="PokeJapan API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)




@app.get("/sets")
def get_sets():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT set_code, set_name, COUNT(*) as card_count
        FROM cards
        WHERE set_code != ''
        GROUP BY set_code, set_name
        ORDER BY set_code DESC
    """)
    rows = cur.fetchall()
    cur.close(); conn.close()
    return {"sets": [dict(r) for r in rows]}

@app.get("/health")
def health():
    conn = get_conn(); cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as cards FROM cards")
    n_cards = cur.fetchone()["cards"]
    cur.execute("SELECT COUNT(*) as prices FROM prices")
    n_prices = cur.fetchone()["prices"]
    cur.close(); conn.close()
    return {
        "status":     "ok",
        "ws_connections": len(manager.active),
        "cards_in_db":    n_cards,
        "prices_in_db":   n_prices,
        "next_scrape_in": max(0, int(SCRAPE_INTERVAL - (time.time() - last_scrape))),
    }

@app.post("/admin/scrape-now")
async def force_scrape(secret: str = Query(...)):
    """Fuerza un scraping inmediato. Útil para poblado inicial."""
    expected = os.environ.get("SEED_SECRET", "pokejapan2025")
    if secret != expected:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Forbidden")
    global last_scrape
    last_scrape = 0  # Resetear timer para que el loop arranque en el siguiente tick
    return {"status": "scraping iniciado — revisa /health en 2-3 minutos"}

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        # Enviar catálogo completo al conectarse
        rows = get_all_products()
        products = []
        for row in rows:
            prices = row.get("prices") or []
            if not prices:
                continue
            ps = sorted(prices, key=lambda x: x["price_jpy"])
            products.append({
                "id":         row["id"],
                "name_ja":    row["name_ja"],
                "name_en":    row["name_en"],
                "set_name":   row["set_name"],
                "set_code":   row["set_code"],
                "rarity":     row["rarity"],
                "image_url":  row["image_url"],
                "detail_url": row["detail_url"],
                "category":   row["category"],
                "cheapest":   {**ps[0], "price_eur": calc_final_eur(ps[0]["price_jpy"])},
                "second":     {**ps[1], "price_eur": calc_final_eur(ps[1]["price_jpy"])} if len(ps) > 1 else None,
            })

        await ws.send_text(json.dumps({
            "type":      "full_catalog",
            "products":  products,
            "total":     len(products),
            "timestamp": datetime.now().isoformat(),
        }, ensure_ascii=False))

        # Mantener viva
        while True:
            await ws.receive_text()

    except WebSocketDisconnect:
        manager.disconnect(ws)
