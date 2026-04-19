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
    conn = get_conn(); cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            id          SERIAL PRIMARY KEY,
            pokecazilla_id  TEXT UNIQUE,   -- ID interno de Pokécazilla (/products/detail/XXXXX)
            name_ja     TEXT NOT NULL,
            name_en     TEXT DEFAULT '',
            set_code    TEXT DEFAULT '',   -- e.g. "sv2a"
            set_name    TEXT DEFAULT '',
            number      TEXT DEFAULT '',   -- e.g. "201/165"
            rarity      TEXT DEFAULT '',   -- e.g. "SAR"
            image_url   TEXT DEFAULT '',
            detail_url  TEXT DEFAULT '',
            category    TEXT DEFAULT 'carta'
                        CHECK(category IN ('carta','caja','promo')),
            created_at  TIMESTAMP DEFAULT NOW(),
            updated_at  TIMESTAMP DEFAULT NOW()
        );
        CREATE TABLE IF NOT EXISTS prices (
            id          SERIAL PRIMARY KEY,
            card_id     INTEGER REFERENCES cards(id) ON DELETE CASCADE,
            source      TEXT NOT NULL,
            price_jpy   INTEGER NOT NULL,
            url         TEXT DEFAULT '',
            scraped_at  TIMESTAMP DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_prices_card
            ON prices(card_id, scraped_at DESC);
        CREATE INDEX IF NOT EXISTS idx_cards_pokecazilla
            ON cards(pokecazilla_id);
        CREATE INDEX IF NOT EXISTS idx_cards_name
            ON cards(name_ja);
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

async def scrape_pokecazilla_page(client: httpx.AsyncClient, url: str) -> list[dict]:
    """
    Scrapa una página de listado de Pokécazilla.
    Selectores verificados contra el HTML real (abril 2025):
      - Cada carta está en un <li> o bloque con:
        - h3 > a  → nombre (con rareza entre paréntesis al final)
        - ### 最低価格 seguido de <a> con ￥precio
        - <img> → imagen
        - texto con [setcode] número (rareza)
        - <a href="/pokemon/products/detail/XXXXX"> → detalle
    """
    results = []
    try:
        r = await client.get(url, timeout=25)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Buscar todos los bloques de producto
        # En el HTML real cada carta está separada por un <a href="/pokemon/products/detail/...">
        # que envuelve la imagen, seguido de h3 y precios
        
        # Estrategia: encontrar todos los detail links y reconstruir cada bloque
        detail_links = soup.find_all("a", href=re.compile(r"/pokemon/products/detail/\d+"))
        
        # También buscar por estructura de encabezados h3
        # Cada producto tiene un bloque tipo:
        # <a href="/products/detail/XXXXX"><img ...></a>
        # <h3><a href="/products/detail/XXXXX">Nombre(SAR)</a></h3>
        # ### 最低価格
        # <a href="...tienda externa...">￥XX,XXX</a>
        # [sv2a] 201/165 (SAR)
        
        seen_ids = set()
        
        for h3 in soup.find_all("h3"):
            a_tag = h3.find("a", href=re.compile(r"/pokemon/products/detail/\d+"))
            if not a_tag:
                continue
            
            href = a_tag.get("href", "")
            detail_id_match = re.search(r"/products/detail/(\d+)", href)
            if not detail_id_match:
                continue
            
            detail_id = detail_id_match.group(1)
            if detail_id in seen_ids:
                continue
            seen_ids.add(detail_id)
            
            # Nombre completo incluyendo rareza
            full_name = a_tag.get_text(strip=True)
            # Separar rareza del nombre: "リザードンex(SAR)" → "リザードンex", "SAR"
            name_match = re.match(r"^(.+?)(?:\(([A-Za-z★◆●☆]+)\))?(?:【(.+?)】)?$", full_name)
            name_ja  = full_name
            rarity_from_name = ""
            if name_match:
                name_ja = name_match.group(1).strip()
                rarity_from_name = name_match.group(2) or ""
                # Si hay 【プロモ】 al final
                if name_match.group(3):
                    rarity_from_name = "PR"

            # Buscar el contenedor padre para encontrar precio e imagen
            # Subir en el DOM para encontrar el bloque completo
            parent = h3.parent
            if not parent:
                continue

            # Precio mínimo: buscar el enlace externo con ￥ cerca del h3
            price_jpy = None
            price_url = ""
            
            # Buscar hacia adelante desde h3 en el mismo contenedor
            # El precio está en un <a> externo que sigue al texto "最低価格"
            for sibling in h3.next_siblings:
                if hasattr(sibling, 'find_all'):
                    # Buscar enlaces con ￥ en el texto
                    for a in sibling.find_all("a", href=True):
                        txt = a.get_text(strip=True)
                        if "￥" in txt or "¥" in txt:
                            jpy = parse_jpy(txt)
                            if jpy:
                                price_jpy = jpy
                                price_url = a.get("href", "")
                                break
                    if price_jpy:
                        break
                elif hasattr(sibling, 'get_text'):
                    txt = sibling.get_text(strip=True)
                    if "￥" in txt or "¥" in txt:
                        jpy = parse_jpy(txt)
                        if jpy:
                            price_jpy = jpy

            if not price_jpy:
                # Intentar buscar en el parent completo
                for a in parent.find_all("a", href=True):
                    href_a = a.get("href","")
                    if "/products/detail/" in href_a:
                        continue
                    txt = a.get_text(strip=True)
                    if ("￥" in txt or "¥" in txt) and len(txt) < 15:
                        jpy = parse_jpy(txt)
                        if jpy:
                            price_jpy = jpy
                            price_url = href_a
                            break

            if not price_jpy:
                continue

            # Imagen
            img_url = ""
            img = parent.find("img")
            if img:
                src = img.get("src", img.get("data-src", ""))
                if src and not "no_image" in src:
                    img_url = src

            # Set code, número y rareza desde el texto del bloque
            set_code = ""
            number   = ""
            rarity   = rarity_from_name
            
            full_text = parent.get_text(" ", strip=True)
            # Buscar patrón [sv2a] 201/165 (SAR)
            set_match = re.search(r'\[([^\]]*)\]\s*([\w/\-]+)\s*\(([A-Za-z★◆●☆]*)\)', full_text)
            if set_match:
                set_code = set_match.group(1).strip()
                number   = set_match.group(2).strip()
                if set_match.group(3).strip():
                    rarity = set_match.group(3).strip()
            
            if not set_code and "SV-P" in full_text:
                set_code = "promo"
                if not rarity:
                    rarity = "PR"

            # Set name aproximado desde set_code
            SET_NAMES = {
                "sv1": "Scarlet & Violet Base", "sv1S": "Scarlet ex",
                "sv1V": "Violet ex", "sv1a": "Triple Beat",
                "sv2": "Snow Hazard / Clay Burst", "sv2a": "Pokemon Card 151",
                "sv2D": "Snow Hazard", "sv2P": "Clay Burst",
                "sv3": "Ruler of the Black Flame", "sv3a": "Raging Surf",
                "sv4": "Ancient Roar / Future Flash", "sv4K": "Ancient Roar",
                "sv4M": "Future Flash", "sv4a": "Shiny Treasure ex",
                "sv5": "Wild Force / Cyber Judge", "sv5K": "Wild Force",
                "sv5M": "Cyber Judge", "sv5a": "Crimson Haze",
                "sv6": "Mask of Change", "sv6a": "Night Wanderer",
                "sv7": "Stellar Miracle", "sv7a": "Paradise Dragona",
                "sv8": "Super Electric Breaker", "sv8a": "Terastal Festival ex",
                "sv9": "Journey Together", "sv9a": "Sowing Storm Reaping Thunder",
                "sv10": "Destined Rivals", "sv11W": "White Hot Arena",
                "sv11B": "Black Bolt", "svG": "Special Deck Set ex",
                "swsh1": "Sword & Shield Base", "swsh3": "Darkness Ablaze",
                "swsh5": "Evolving Skies", "swsh12": "Silver Tempest",
                "promo": "SV Promo", "WCS23": "World Championship 2023",
            }
            set_name = SET_NAMES.get(set_code, set_code)

            category = guess_category(name_ja + full_name, rarity)

            detail_url = f"https://pokecazilla.com/pokemon/products/detail/{detail_id}"

            results.append({
                "pokecazilla_id": detail_id,
                "name_ja":    name_ja,
                "set_code":   set_code,
                "set_name":   set_name,
                "number":     number,
                "rarity":     rarity,
                "image_url":  img_url,
                "detail_url": detail_url,
                "category":   category,
                "price_jpy":  price_jpy,
                "price_url":  price_url,
            })

    except Exception as e:
        log.error(f"Error scraping {url}: {e}")

    return results

async def scrape_pokecazilla_all(client: httpx.AsyncClient) -> list[dict]:
    """Scrapa múltiples páginas de Pokécazilla."""
    all_results = []
    
    for base_url in POKECAZILLA_URLS:
        # Scrapear hasta 5 páginas por categoría (100 cartas por página)
        for page in range(1, 6):
            url = f"{base_url}&page={page}"
            log.info(f"Scraping Pokécazilla: {url}")
            
            results = await scrape_pokecazilla_page(client, url)
            
            if not results:
                log.info(f"  → Sin resultados en página {page}, parando")
                break
            
            all_results.extend(results)
            log.info(f"  → {len(results)} productos en página {page}")
            
            await asyncio.sleep(3)  # Respetar rate limit
    
    log.info(f"Pokécazilla total: {len(all_results)} productos")
    return all_results

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

async def main_loop():
    global last_scrape
    async with httpx.AsyncClient(
        headers=HEADERS,
        follow_redirects=True,
        timeout=30.0,
    ) as client:
        while True:
            now = time.time()
            if now - last_scrape >= SCRAPE_INTERVAL:
                log.info("=== Iniciando scraping Pokécazilla ===")
                try:
                    results = await scrape_pokecazilla_all(client)
                    if results:
                        await process_results(results)
                    last_scrape = time.time()
                    log.info("=== Scraping completado ===")
                except Exception as e:
                    log.error(f"Error en main loop: {e}")
            
            await asyncio.sleep(60)

# ─── FastAPI ─────────────────────────────────────────────────────────────────

app = FastAPI(title="PokeJapan API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def startup():
    init_db()
    # Lanzar scraping inmediatamente al arrancar
    asyncio.create_task(main_loop())
    log.info("PokeJapan API arrancada — scraping iniciará en segundos")

@app.get("/products")
def get_products(
    category: str  = Query(None),
    search:   str  = Query(None),
    rarity:   str  = Query(None),
    set_code: str  = Query(None),
    sort:     str  = Query("rarity"),
    limit:    int  = Query(200),
    offset:   int  = Query(0),
):
    rows = get_all_products()
    out  = []

    for row in rows:
        prices = row.get("prices") or []
        if not prices:
            continue
        if category and row["category"] != category:
            continue
        if set_code and row["set_code"] != set_code:
            continue
        if rarity and row["rarity"] != rarity:
            continue
        if search:
            q = search.lower()
            if (q not in row["name_ja"].lower()
                and q not in (row["name_en"] or "").lower()
                and q not in (row["set_name"] or "").lower()
                and q not in (row["rarity"] or "").lower()):
                continue

        prices_sorted = sorted(prices, key=lambda x: x["price_jpy"])
        cheapest = prices_sorted[0]
        second   = prices_sorted[1] if len(prices_sorted) > 1 else None

        out.append({
            "id":              row["id"],
            "pokecazilla_id":  row["pokecazilla_id"],
            "name_ja":         row["name_ja"],
            "name_en":         row["name_en"],
            "set_code":        row["set_code"],
            "set_name":        row["set_name"],
            "number":          row["number"],
            "rarity":          row["rarity"],
            "image_url":       row["image_url"],
            "detail_url":      row["detail_url"],
            "category":        row["category"],
            "cheapest": {
                **cheapest,
                "price_eur": calc_final_eur(cheapest["price_jpy"]),
            },
            "second": {
                **second,
                "price_eur": calc_final_eur(second["price_jpy"]),
            } if second else None,
        })

    # Ordenar
    if sort == "price_asc":
        out.sort(key=lambda x: x["cheapest"]["price_jpy"])
    elif sort == "price_desc":
        out.sort(key=lambda x: x["cheapest"]["price_jpy"], reverse=True)
    # "rarity" ya viene ordenado por la query SQL

    total = len(out)
    return {"products": out[offset:offset+limit], "total": total}

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
