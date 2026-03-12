#!/usr/bin/env python3
"""
Genius Arena Discord Bot
"""

import sqlite3
import json
import sys
import os
import asyncio
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "TU_TOKEN_AQUI")
CHANNEL_ID    = int(os.getenv("CHANNEL_ID", "0"))   # ID numérico del canal
CHECK_EVERY_HOURS = 0.5                             # Frecuencia de chequeo

URL      = "https://app2.genius-arena.com/challenge/23/talent-hackathon-2026"
DB_FILE  = "genius_arena.db"
JSON_DIR = "runs"
HEADLESS = True
TIMEOUT  = 30_000


def validate_config():
    """Revisa la configuracion"""
    errors = []
    if DISCORD_TOKEN == "TU_TOKEN_AQUI":
        errors.append("❌  DISCORD_TOKEN no configurado.")
    if CHANNEL_ID == 0:
        errors.append("❌  CHANNEL_ID no configurado.")
    if errors:
        for e in errors:
            print(e)
        print("\n💡  Edita las variables DISCORD_TOKEN y CHANNEL_ID en el script,")
        print("    o usa variables de entorno:")
        print("    DISCORD_TOKEN=xxx CHANNEL_ID=yyy python3 discord_bot.py")
        sys.exit(1)


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


# ── Scraping (Playwright) ─────────────────────────────────────────────────────
EXTRACT_JS = """
() => {
    const results = [];
    const cards = document.querySelectorAll(
        ".bubble-element.group-item.bubble-r-container.flex.column[class*='entry-']"
    );
    cards.forEach(card => {
        const titleEl = card.querySelector(".cmjdn1");
        const title = titleEl ? titleEl.innerText.trim() : "";
        if (!title) return;

        let team = "", category = "", description = "";
        const textEls = Array.from(card.querySelectorAll(".bubble-element.Text"));
        textEls.forEach((el, i) => {
            const txt = el.innerText.trim();
            const next = textEls[i + 1];
            if (txt === "Equipo"      && next) team        = next.innerText.trim();
            if (txt === "Categoría"   && next) category    = next.innerText.trim();
            if (txt === "Descripción" && next) description = next.innerText.trim();
        });

        const memberImgs = card.querySelectorAll(".cmaOgaA0 img[alt]");
        const members = Array.from(memberImgs)
            .map(img => img.getAttribute("alt")).filter(Boolean);

        const link  = card.querySelector("a[href*='participation_info']");
        const url   = link ? link.href : "";
        const match = url.match(/team_id=(\d+)/);
        const id    = match ? match[1] : title.toLowerCase().replace(/[^a-z0-9]+/g, "-");

        results.push({ id, title, team, category, description,
                       members: members.join(", "), url });
    });
    return results;
}
"""

PAGES_JS = """
() => {
    const btns = document.querySelectorAll(".cmsaXp0");
    return Array.from(btns).map(b => b.innerText.trim()).filter(t => /^\\d+$/.test(t));
}
"""


def scrape_sync():
    """Scraping síncrono — se llama desde un thread separado."""
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    def extract(page):
        page.wait_for_timeout(1800)
        return page.evaluate(EXTRACT_JS)

    all_projects, visited = [], set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()

        try:
            page.goto(URL, timeout=TIMEOUT, wait_until="networkidle")
        except PWTimeout:
            page.goto(URL, timeout=TIMEOUT)
            page.wait_for_timeout(3000)

        # Clic en pestaña Proyectos
        clicked = False
        for attempt in [
            lambda: page.locator("h6.cmaOcaP0").filter(has_text="Proyectos").first.click(),
            lambda: page.get_by_text("Proyectos", exact=True).first.click(),
        ]:
            try:
                attempt()
                clicked = True
                break
            except Exception:
                pass

        try:
            page.wait_for_selector(
                ".bubble-element.group-item.bubble-r-container.flex.column[class*='entry-']",
                timeout=15_000
            )
        except PWTimeout:
            page.wait_for_timeout(4000)

        # Scroll para lazy loading
        for _ in range(4):
            page.evaluate("window.scrollBy(0, 700)")
            page.wait_for_timeout(400)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(500)

        # Página 1
        cards = extract(page)
        all_projects.extend(cards)
        visited.add("1")

        # Resto de páginas
        while True:
            buttons  = page.evaluate(PAGES_JS)
            next_pg  = next((b for b in buttons if b not in visited), None)
            if not next_pg:
                break
            try:
                page.locator(".cmsaXp0").filter(has_text=next_pg).first.click()
                page.wait_for_timeout(2200)
            except PWTimeout:
                break
            cards = extract(page)
            all_projects.extend(cards)
            visited.add(next_pg)

        browser.close()

    # Deduplicar
    seen_ids, deduped = set(), []
    for p in all_projects:
        if p["id"] not in seen_ids:
            deduped.append(p)
            seen_ids.add(p["id"])
    return deduped


def save_run_json(timestamp, new_projects, seen_projects):
    Path(JSON_DIR).mkdir(exist_ok=True)
    safe_ts  = timestamp.replace(":", "-").replace(" ", "_")
    filepath = Path(JSON_DIR) / f"run_{safe_ts}.json"
    payload  = {
        "run_timestamp": timestamp,
        "summary": {
            "total_found":  len(new_projects) + len(seen_projects),
            "new_projects": len(new_projects),
            "already_seen": len(seen_projects),
        },
        "new_projects":          new_projects,
        "already_seen_projects": seen_projects,
    }
    filepath.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(filepath)


# ── Discord Bot ───────────────────────────────────────────────────────────────
try:
    import discord
    from discord.ext import tasks
except ImportError:
    sys.exit("❌  Instala discord.py:\n    pip install discord.py")


class GeniusArenaBot(discord.Client):

    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.channel_id = CHANNEL_ID

    async def on_ready(self):
        print(f"✅  Bot conectado como {self.user}")
        print(f"📡  Canal objetivo: {self.channel_id}")
        print(f"⏱   Checando cada {CHECK_EVERY_HOURS}h\n")

        ch = self.get_channel(self.channel_id)
        if ch:
            await ch.send(
                embed=self._embed_startup()
            )
        self.check_loop.start()

    def _embed_startup(self):
        e = discord.Embed(
            title="🚀 Genius Arena Scraper activo",
            description=(
                f"Monitoreando proyectos del **Talent Hackathon 2026**\n"
                f"Checaré cada **{CHECK_EVERY_HOURS} horas** y te avisaré aquí."
            ),
            color=0x4651E2,
        )
        e.add_field(name="🔗 URL", value=URL, inline=False)
        e.set_footer(text=f"Iniciado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        return e

    @tasks.loop(hours=CHECK_EVERY_HOURS)
    async def check_loop(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] 🔍 Corriendo scraper …")

        ch = self.get_channel(self.channel_id)

        # Scraping en thread separado para no bloquear el event loop
        loop = asyncio.get_event_loop()
        try:
            all_projects = await loop.run_in_executor(None, scrape_sync)
        except Exception as exc:
            print(f"  ❌ Error en scraping: {exc}")
            if ch:
                await ch.send(
                    embed=discord.Embed(
                        title="⚠️ Error en el scraper",
                        description=f"```{exc}```",
                        color=0xFF4444,
                    )
                )
            return

        conn = sqlite3.connect(DB_FILE)
        init_db(conn)
        prev_ids = get_seen_ids(conn)

        new_projects  = [p for p in all_projects if p["id"] not in prev_ids]
        seen_projects = [p for p in all_projects if p["id"] in     prev_ids]

        save_new_projects(conn, new_projects, now)
        json_file = save_run_json(now, new_projects, seen_projects)
        log_run(conn, now, len(all_projects), len(new_projects), json_file)
        conn.close()

        print(f"  Total: {len(all_projects)} | Nuevos: {len(new_projects)} | Vistos: {len(seen_projects)}")

        if ch:
            if new_projects:
                # Un embed por cada proyecto nuevo
                await ch.send(
                    embed=self._embed_summary(len(new_projects), len(all_projects))
                )
                for p in new_projects:
                    await ch.send(embed=self._embed_project(p))
                    await asyncio.sleep(0.5)   # rate-limit suave
            else:
                await ch.send(
                    embed=self._embed_no_new(len(all_projects), now)
                )

    @check_loop.before_loop
    async def before_check(self):
        await self.wait_until_ready()

    # ── Embeds ────────────────────────────────────────────────────────────────
    def _embed_summary(self, new_count, total):
        e = discord.Embed(
            title=f"🆕 {new_count} proyecto{'s' if new_count > 1 else ''} nuevo{'s' if new_count > 1 else ''} detectado{'s' if new_count > 1 else ''}",
            color=0x2ECC71,
            timestamp=datetime.now(),
        )
        e.add_field(name="📊 Total en el hackathon", value=str(total), inline=True)
        e.add_field(name="✨ Nuevos esta revisión",  value=str(new_count), inline=True)
        e.set_footer(text="Genius Arena Scraper")
        return e

    def _embed_project(self, p):
        e = discord.Embed(
            title=p["title"],
            url=p["url"] if p["url"] else discord.Embed.Empty,
            color=0x4651E2,
        )
        if p["team"]:
            e.add_field(name="👥 Equipo",     value=p["team"],     inline=True)
        if p["category"]:
            e.add_field(name="🏷️ Categoría", value=p["category"], inline=False)
        if p["description"]:
            desc = p["description"][:300] + ("…" if len(p["description"]) > 300 else "")
            e.add_field(name="📝 Descripción", value=desc, inline=False)
        if p["members"]:
            e.add_field(name="🙋 Participantes", value=p["members"], inline=False)
        if p["url"]:
            e.add_field(name="🔗 Ver equipo", value=f"[Ver más]({p['url']})", inline=False)
        e.set_footer(text=f"ID: {p['id']}")
        return e

    def _embed_no_new(self, total, timestamp):
        e = discord.Embed(
            title="✅ Sin proyectos nuevos",
            description=f"Se revisaron **{total}** proyectos — ninguno es nuevo.",
            color=0x95A5A6,
            timestamp=datetime.now(),
        )
        e.set_footer(text="Próxima revisión en 2 horas • Genius Arena Scraper")
        return e


# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    validate_config()
    init_db(sqlite3.connect(DB_FILE))   # asegura que la BD exista
    bot = GeniusArenaBot()
    bot.run(DISCORD_TOKEN)
