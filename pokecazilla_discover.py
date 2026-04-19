"""
pokecazilla_discover.py
Ejecuta esto UNA VEZ para descubrir los pack_id reales de cada set
y actualizar el diccionario POKECAZILLA_PACKS en main.py

Uso: python pokecazilla_discover.py
"""

import requests
from bs4 import BeautifulSoup
import json

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0",
    "Accept-Language": "ja-JP,ja;q=0.9",
}

SERIES = [
    ("スカーレット&バイオレット", 15),  # Scarlet & Violet
    ("ソード&シールド",           14),  # Sword & Shield
    ("サン＆ムーン",              13),  # Sun & Moon
]

def discover_packs():
    packs = {}

    for series_name, category_id in SERIES:
        url = f"https://pokecazilla.com/pokemon/products/list?category_id={category_id}"
        try:
            r    = requests.get(url, headers=HEADERS, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")

            # Los packs aparecen en el menú lateral o en la URL de cada set
            # Buscar enlaces con pack_id en la URL
            pack_links = soup.select("a[href*='pack_id']")

            for link in pack_links:
                href  = link.get("href", "")
                name  = link.get_text(strip=True)
                # Extraer pack_id de la URL
                import re
                match = re.search(r'pack_id=(\d+)', href)
                if match:
                    pack_id = int(match.group(1))
                    packs[name] = {
                        "category_id": category_id,
                        "pack_id":     pack_id,
                        "series":      series_name,
                    }
                    print(f"  {series_name} | {name} → pack_id={pack_id}")

        except Exception as e:
            print(f"Error {series_name}: {e}")

    # Guardar resultado
    with open("pokecazilla_packs.json", "w", encoding="utf-8") as f:
        json.dump(packs, f, ensure_ascii=False, indent=2)

    print(f"\nTotal: {len(packs)} packs guardados en pokecazilla_packs.json")
    print("\nCopia los pack_id que te interesen al diccionario POKECAZILLA_PACKS en main.py")
    return packs


if __name__ == "__main__":
    discover_packs()
