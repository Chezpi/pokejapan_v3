from fastapi import FastAPI
import httpx
from bs4 import BeautifulSoup
import re

app = FastAPI()

# =========================
# HEALTH CHECK
# =========================
@app.get("/health")
def health():
    return {"status": "ok"}

# =========================
# HELPERS
# =========================
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
    match = re.search(r'([\d,]+)', text)
    if match:
        return int(match.group(1).replace(",", ""))
    return 0

def extract_psa(text):
    match = re.search(r'PSA\s?(\d+)', text.upper())
    if match:
        return f"PSA {match.group(1)}"
    return None

# =========================
# SCRAPER CARDRUSH
# =========================
async def scrape_cardrush(query="リザードン"):
    url = f"https://www.cardrush-pokemon.jp/product-list?keyword={query}"

    headers = {"User-Agent": "Mozilla/5.0"}

    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=headers)

    soup = BeautifulSoup(r.text, "lxml")

    products = []
    items = soup.select(".product_item")

    for i, item in enumerate(items[:20]):
        text = item.get_text(" ", strip=True)

        name = text[:100]
        price = extract_price(text)
        condition = map_condition(text)
        psa = extract_psa(text)

        link_tag = item.select_one("a")
        link = "https://www.cardrush-pokemon.jp" + link_tag["href"] if link_tag else ""

        products.append({
            "id": i,
            "name": name,
            "name_ja": name,
            "sources": [
                {
                    "src": "cardrush",
                    "jpy": price,
                    "condition": condition,
                    "grade": psa,
                    "url": link
                }
            ]
        })

    return products

# =========================
# ENDPOINT PRODUCTS
# =========================
@app.get("/products")
async def get_products(q: str = "リザードン"):
    print(f"🔍 Buscando: {q}")

    products = await scrape_cardrush(q)

    return {
        "products": products,
        "total": len(products)
    }

# =========================
# ROOT (para probar rápido)
# =========================
@app.get("/")
def root():
    return {"msg": "PokeJapan API funcionando 🚀"}