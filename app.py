#!/usr/bin/env python3
"""
Genius Arena - Scraper de Proyectos
====================================
Detecta proyectos nuevos en: https://app2.genius-arena.com/challenge/23/talent-hackathon-2026
- Hace clic en la pestaña "Proyectos"
- Navega por todas las páginas de paginación
- Guarda proyectos vistos en SQLite
- Genera un JSON por cada ejecución con proyectos nuevos y ya vistos

Uso:
    pip install playwright
    playwright install chromium
    python3 genius_arena_scraper.py
"""

import sqlite3
import json
import sys
from datetime import datetime
from pathlib import Path

# ── Configuración ──────────────────────────────────────────────────────────────
URL      = "https://app2.genius-arena.com/challenge/23/talent-hackathon-2026"
DB_FILE  = "genius_arena.db"
JSON_DIR = "runs"
HEADLESS = True
TIMEOUT  = 30_000   # ms


# ── Base de datos ──────────────────────────────────────────────────────────────
def init_db(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id          TEXT PRIMARY KEY,
            title       TEXT,
            team        TEXT,
            category    TEXT,
            description TEXT,
            members     TEXT,
            url         TEXT,
            first_seen  TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
            run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            total_found INTEGER,
            new_count   INTEGER,
            json_file   TEXT
        );
    """)
    conn.commit()


def get_seen_ids(conn):
    return {r[0] for r in conn.execute("SELECT id FROM projects").fetchall()}


def save_new_projects(conn, projects, now):
    for p in projects:
        conn.execute(
            """INSERT OR IGNORE INTO projects
               (id, title, team, category, description, members, url, first_seen)
               VALUES (:id,:title,:team,:category,:description,:members,:url,:first_seen)""",
            {**p, "first_seen": now},
        )
    conn.commit()


def log_run(conn, timestamp, total, new_count, json_file):
    conn.execute(
        "INSERT INTO runs (timestamp, total_found, new_count, json_file) VALUES (?,?,?,?)",
        (timestamp, total, new_count, json_file),
    )
    conn.commit()


# ── JavaScript: extrae tarjetas de la página actual ───────────────────────────
EXTRACT_JS = """
() => {
    const results = [];

    // Cada proyecto es un .entry-N dentro del repeating group principal
    // Usamos el patrón de clases del HTML real
    const cards = document.querySelectorAll(
        ".bubble-element.group-item.bubble-r-container.flex.column[class*='entry-']"
    );

    cards.forEach(card => {
        // -- Título (texto grande, font-weight 600) --
        const titleEl = card.querySelector(".cmjdn1");
        const title = titleEl ? titleEl.innerText.trim() : "";
        if (!title) return;   // descartamos elementos sin título

        // -- Equipo, Categoría, Descripción --
        // El patrón es: label (texto gris) seguido del valor
        let team = "", category = "", description = "";
        const textEls = Array.from(card.querySelectorAll(".bubble-element.Text"));

        textEls.forEach((el, i) => {
            const txt = el.innerText.trim();
            const next = textEls[i + 1];
            if (txt === "Equipo"      && next) team        = next.innerText.trim();
            if (txt === "Categoría"   && next) category    = next.innerText.trim();
            if (txt === "Descripción" && next) description = next.innerText.trim();
        });

        // -- Participantes: atributo alt de las imágenes de avatar --
        const memberImgs = card.querySelectorAll(".cmaOgaA0 img[alt]");
        const members = Array.from(memberImgs)
            .map(img => img.getAttribute("alt"))
            .filter(Boolean);

        // -- URL y team_id --
        const link = card.querySelector("a[href*='participation_info']");
        const url  = link ? link.href : "";
        const match = url.match(/team_id=(\d+)/);
        const id = match
            ? match[1]
            : title.toLowerCase().replace(/[^a-z0-9]+/g, "-");

        results.push({
            id,
            title,
            team,
            category,
            description,
            members: members.join(", "),
            url
        });
    });

    return results;
}
"""

# ── JavaScript: obtiene los números de página disponibles ─────────────────────
PAGES_JS = """
() => {
    // Los botones de paginación tienen la clase cmsaXp0
    const btns = document.querySelectorAll(".cmsaXp0");
    return Array.from(btns)
        .map(b => b.innerText.trim())
        .filter(t => /^\\d+$/.test(t));
}
"""


# ── Extrae todas las páginas navegando con paginación ─────────────────────────
def scrape_all_pages(page):
    from playwright.sync_api import TimeoutError as PWTimeout

    all_projects  = []
    visited_pages = set()

    def current_cards():
        page.wait_for_timeout(1800)
        return page.evaluate(EXTRACT_JS)

    def page_buttons():
        return page.evaluate(PAGES_JS)

    # Página 1
    cards = current_cards()
    all_projects.extend(cards)
    visited_pages.add("1")
    print(f"   Página 1 → {len(cards)} proyectos")

    while True:
        buttons = page_buttons()
        next_pg = next((b for b in buttons if b not in visited_pages), None)
        if not next_pg:
            break

        print(f"   Navegando a página {next_pg} …", end=" ", flush=True)
        try:
            # Localizamos el botón por texto exacto dentro de .cmsaXp0
            btn = page.locator(".cmsaXp0").filter(has_text=next_pg).first
            btn.scroll_into_view_if_needed()
            btn.click()
            page.wait_for_timeout(2200)
        except PWTimeout:
            print(f"timeout, deteniendo paginación.")
            break

        cards = current_cards()
        all_projects.extend(cards)
        visited_pages.add(next_pg)
        print(f"{len(cards)} proyectos")

    return all_projects


# ── Scraping principal con Playwright ─────────────────────────────────────────
def scrape():
    try:
        from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
    except ImportError:
        sys.exit(
            "❌  Playwright no instalado.\n"
            "    Ejecuta:  pip install playwright && playwright install chromium"
        )

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        ctx     = browser.new_context(viewport={"width": 1280, "height": 900})
        page    = ctx.new_page()

        # ── Cargar página principal ────────────────────────────────────────────
        print(f"🌐  Navegando a {URL} …")
        try:
            page.goto(URL, timeout=TIMEOUT, wait_until="networkidle")
        except PWTimeout:
            page.goto(URL, timeout=TIMEOUT)
            page.wait_for_timeout(3000)

        # ── Clic en la pestaña "Proyectos" ────────────────────────────────────
        print('🖱   Haciendo clic en pestaña "Proyectos" …')
        clicked = False

        # Intento 1: selector exacto del HTML proporcionado
        try:
            tab = page.locator("h6.cmaOcaP0").filter(has_text="Proyectos").first
            tab.wait_for(timeout=8_000)
            tab.click()
            clicked = True
        except PWTimeout:
            pass

        # Intento 2: cualquier elemento de texto con "Proyectos"
        if not clicked:
            try:
                page.get_by_text("Proyectos", exact=True).first.click()
                clicked = True
            except Exception:
                pass

        if clicked:
            print('✅  Clic en "Proyectos" realizado')
        else:
            print('⚠️  No se encontró la pestaña "Proyectos", continuando de todas formas …')

        # Esperar que aparezcan las tarjetas de proyectos
        try:
            page.wait_for_selector(
                ".bubble-element.group-item.bubble-r-container.flex.column[class*='entry-']",
                timeout=15_000
            )
        except PWTimeout:
            page.wait_for_timeout(4000)

        # Scroll suave para activar lazy loading
        for _ in range(4):
            page.evaluate("window.scrollBy(0, 700)")
            page.wait_for_timeout(400)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(500)

        # ── Extraer todas las páginas ──────────────────────────────────────────
        print("📄  Extrayendo proyectos …")
        projects = scrape_all_pages(page)

        browser.close()

    # Deduplicar por id (por si una tarjeta apareció en varias páginas)
    seen_ids, deduped = set(), []
    for p in projects:
        if p["id"] not in seen_ids:
            deduped.append(p)
            seen_ids.add(p["id"])

    return deduped


# ── Guardar JSON del run ───────────────────────────────────────────────────────
def save_run_json(timestamp, new_projects, seen_projects):
    run_dir  = Path(JSON_DIR)
    run_dir.mkdir(exist_ok=True)
    safe_ts  = timestamp.replace(":", "-").replace(" ", "_")
    filepath = run_dir / f"run_{safe_ts}.json"

    payload = {
        "run_timestamp": timestamp,
        "summary": {
            "total_found":  len(new_projects) + len(seen_projects),
            "new_projects": len(new_projects),
            "already_seen": len(seen_projects),
        },
        "new_projects":          new_projects,
        "already_seen_projects": seen_projects,
    }

    filepath.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )
    return str(filepath)


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*55}")
    print(f"  Genius Arena Scraper  —  {now}")
    print(f"{'='*55}\n")

    conn = sqlite3.connect(DB_FILE)
    init_db(conn)

    prev_ids = get_seen_ids(conn)
    print(f"📦  Proyectos en BD antes del run: {len(prev_ids)}\n")

    # ── Scraping ───────────────────────────────────────────────────────────────
    all_projects = scrape()
    print(f"\n🔍  Total encontrados: {len(all_projects)}")

    if not all_projects:
        print("⚠️  No se encontraron proyectos.")
        print("    Sugerencias:")
        print("    · Cambia HEADLESS = False para ver qué ocurre en el navegador.")
        print("    · Verifica si el sitio requiere login.")
        conn.close()
        return

    # ── Clasificar ─────────────────────────────────────────────────────────────
    new_projects  = [p for p in all_projects if p["id"] not in prev_ids]
    seen_projects = [p for p in all_projects if p["id"] in     prev_ids]

    print(f"🆕  Nuevos:     {len(new_projects)}")
    print(f"👁   Ya vistos: {len(seen_projects)}")

    if new_projects:
        print("\n📋  Proyectos NUEVOS detectados:")
        for p in new_projects:
            line = f"   • [{p['id']}] {p['title']}"
            if p["team"]:
                line += f"  (Equipo: {p['team']})"
            print(line)
            if p["category"]:
                print(f"          Categoría: {p['category']}")
            if p["members"]:
                print(f"          Miembros:  {p['members']}")

    # ── Persistir ──────────────────────────────────────────────────────────────
    save_new_projects(conn, new_projects, now)

    json_file = save_run_json(now, new_projects, seen_projects)
    print(f"\n💾  JSON guardado en: {json_file}")

    log_run(conn, now, len(all_projects), len(new_projects), json_file)
    conn.close()

    print(f"✅  Run completado. BD: {DB_FILE}\n")


if __name__ == "__main__":
    main()
