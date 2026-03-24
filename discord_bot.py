#!/usr/bin/env python3
"""
Genius Arena Discord Bot
=========================
- Scraper automático cada 2 horas
- /buscar <término>       → busca proyectos en la BD
- /summary                → cuántos proyectos hay por categoría
- /banco_azteca           → proyectos de Grupo Salinas Banco Azteca
- /fundacion_coppel       → proyectos de Fundación Coppel
- /mcdonalds              → proyectos de McDonald's
- /salud_digna            → proyectos de Salud Digna
- /toka                   → proyectos de Toka
- /qualcomm               → proyectos de Qualcomm
- /capital_one            → proyectos de Capital One
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

DISCORD_TOKEN     = os.getenv("DISCORD_TOKEN", "TU_TOKEN_AQUI")
CHANNEL_ID        = int(os.getenv("CHANNEL_ID", "0"))
GUILD_ID          = int(os.getenv("GUILD_ID", "0"))   # ID de tu server → sync instantáneo
CHECK_EVERY_HOURS = 0.5

URL      = "https://app2.genius-arena.com/challenge/23/talent-hackathon-2026"
DB_FILE  = "genius_arena.db"
JSON_DIR = "runs"
HEADLESS = True
TIMEOUT  = 30_000
 
# Mapeo de slash command → fragmento de categoría en la BD
CATEGORIES = {
    "banco_azteca":    "Grupo Salinas Banco Azteca",
    "fundacion_coppel":"Fundación Coppel",
    "mcdonalds":       "McDonald's",
    "salud_digna":     "Salud Digna",
    "toka":            "Toka",
    "qualcomm":        "Qualcomm",
    "capital_one":     "Capital One",
}
 
# Emoji por categoría
CAT_EMOJI = {
    "banco_azteca":     "🏦",
    "fundacion_coppel": "⚽",
    "mcdonalds":        "🍔",
    "salud_digna":      "🏥",
    "toka":             "🎮",
    "qualcomm":         "⚡",
    "capital_one":      "💳",
}
 
# Color embed por categoría
CAT_COLOR = {
    "banco_azteca":     0xE63329,
    "fundacion_coppel": 0x0057A8,
    "mcdonalds":        0xFFC72C,
    "salud_digna":      0x00A651,
    "toka":             0x7B2FBE,
    "qualcomm":         0x3253DC,
    "capital_one":      0xD03027,
}
 
 
# ── Validación ────────────────────────────────────────────────────────────────
def validate_config():
    errors = []
    if DISCORD_TOKEN == "TU_TOKEN_AQUI":
        errors.append("❌  DISCORD_TOKEN no configurado en .env")
    if CHANNEL_ID == 0:
        errors.append("❌  CHANNEL_ID no configurado en .env")
    if GUILD_ID == 0:
        errors.append("❌  GUILD_ID no configurado en .env")
    if errors:
        for e in errors:
            print(e)
        sys.exit(1)
 
 
# ── Base de datos ─────────────────────────────────────────────────────────────
def get_conn():
    return sqlite3.connect(DB_FILE)
 
 
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
 
 
# ── Queries de búsqueda ───────────────────────────────────────────────────────
def db_search(term: str) -> list[dict]:
    """Busca proyectos por término en título, equipo, descripción o miembros."""
    like = f"%{term}%"
    conn = get_conn()
    rows = conn.execute(
        """SELECT id, title, team, category, description, members, url, first_seen
           FROM projects
           WHERE title       LIKE ? COLLATE NOCASE
              OR team        LIKE ? COLLATE NOCASE
              OR description LIKE ? COLLATE NOCASE
              OR members     LIKE ? COLLATE NOCASE
           ORDER BY first_seen DESC""",
        (like, like, like, like),
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]
 
 
def db_by_category(keyword: str) -> list[dict]:
    """Devuelve proyectos cuya categoría contiene keyword."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT id, title, team, category, description, members, url, first_seen
           FROM projects
           WHERE category LIKE ? COLLATE NOCASE
           ORDER BY first_seen DESC""",
        (f"%{keyword}%",),
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]
 
 
def db_summary() -> list[tuple]:
    """Retorna (categoria, count) ordenado de mayor a menor."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT category, COUNT(*) as cnt
           FROM projects
           GROUP BY category
           ORDER BY cnt DESC"""
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    conn.close()
    return rows, total
 
 
def _row_to_dict(r) -> dict:
    return {
        "id": r[0], "title": r[1], "team": r[2], "category": r[3],
        "description": r[4], "members": r[5], "url": r[6], "first_seen": r[7],
    }
 
 
# ── Scraping ──────────────────────────────────────────────────────────────────
EXTRACT_JS = r"""
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
 
# JS: obtiene el estado de la paginación
# Devuelve { active, pages, maxVisible }
# - active: número de página actualmente activa (resaltada)
# - pages: lista de números visibles
# - maxVisible: el número más alto visible en los botones
PAGE_STATE_JS = r"""
() => {
    // Botones numéricos: .cmsaXp0 (los de número) y buscamos cuál está activo
    // El activo suele tener color distinto o background diferente
    const numBtns = Array.from(document.querySelectorAll(".cmsaXp0"));
    const pages = numBtns.map(b => parseInt(b.innerText.trim())).filter(n => !isNaN(n));
 
    // Detectar página activa: buscamos el que tenga color de texto oscuro/diferente
    // o background highlight vs los demás
    let active = null;
    numBtns.forEach(b => {
        const n = parseInt(b.innerText.trim());
        if (isNaN(n)) return;
        const style = window.getComputedStyle(b);
        const color = style.color;
        const bg = style.backgroundColor;
        // Activo suele tener texto más oscuro o bg coloreado
        const isActive = bg !== "rgba(0, 0, 0, 0)" && bg !== "rgb(255, 255, 255)" && bg !== "transparent";
        if (isActive && active === null) active = n;
    });
 
    // Fallback: si no detectamos por color, el activo es el primero del grupo visible
    if (active === null && pages.length > 0) active = pages[0];
 
    return {
        active,
        pages,
        maxVisible: pages.length > 0 ? Math.max(...pages) : 0
    };
}
"""
 
 
def scrape_sync():
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
 
    def extract(page):
        page.wait_for_timeout(1800)
        return page.evaluate(EXTRACT_JS)
 
    def get_page_state(page):
        return page.evaluate(PAGE_STATE_JS)
 
    def click_page_number(page, number):
        """Hace clic en un botón numérico específico."""
        btn = page.locator(".cmsaXp0").filter(has_text=str(number)).first
        btn.scroll_into_view_if_needed()
        btn.click()
 
    def click_next_group(page):
        """Hace clic en el botón › para avanzar al siguiente grupo de 5 páginas."""
        btn = page.locator(".cmmtaN").last
        btn.scroll_into_view_if_needed()
        btn.click()
 
    all_projects = []
    visited_pages = set()   # páginas ya scrapeadas (por número)
 
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=HEADLESS)
        ctx = browser.new_context(viewport={"width": 1280, "height": 900})
        page = ctx.new_page()
 
        try:
            page.goto(URL, timeout=TIMEOUT, wait_until="networkidle")
        except PWTimeout:
            page.goto(URL, timeout=TIMEOUT)
            page.wait_for_timeout(3000)
 
        for attempt in [
            lambda: page.locator("h6.cmaOcaP0").filter(has_text="Proyectos").first.click(),
            lambda: page.get_by_text("Proyectos", exact=True).first.click(),
        ]:
            try:
                attempt(); break
            except Exception:
                pass
 
        try:
            page.wait_for_selector(
                ".bubble-element.group-item.bubble-r-container.flex.column[class*='entry-']",
                timeout=15_000)
        except PWTimeout:
            page.wait_for_timeout(4000)
 
        for _ in range(4):
            page.evaluate("window.scrollBy(0, 700)")
            page.wait_for_timeout(400)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(500)
 
        consecutive_empty = 0   # protección contra loops infinitos
 
        while True:
            state = get_page_state(page)
            visible_pages = state["pages"]
            active_page   = state["active"]
 
            # Páginas visibles que aún no hemos scrapeado
            pending = [p for p in visible_pages if p not in visited_pages]
 
            if not pending:
                # Todas las visibles ya están scrapeadas → intentar avanzar grupo
                prev_max = state["maxVisible"]
                print(f"   Avanzando al siguiente grupo (max visible: {prev_max})...")
                try:
                    click_next_group(page)
                    page.wait_for_timeout(2500)
                except Exception as e:
                    print(f"   No se pudo avanzar: {e}")
                    break
 
                new_state = get_page_state(page)
                if new_state["maxVisible"] <= prev_max:
                    # El grupo no cambió → estamos en la última página
                    print("   Fin: el grupo de paginas no cambio, ultima pagina alcanzada.")
                    break
 
                consecutive_empty = 0
                continue
 
            # Scrapear cada página pendiente del grupo visible
            for pg_num in sorted(pending):
                if pg_num != active_page:
                    try:
                        click_page_number(page, pg_num)
                        page.wait_for_timeout(2200)
                    except Exception as e:
                        print(f"   Error al clicar pagina {pg_num}: {e}")
                        continue
 
                cards = extract(page)
                visited_pages.add(pg_num)
                all_projects.extend(cards)
                print(f"   Pagina {pg_num} -> {len(cards)} proyectos (total: {len(all_projects)})")
 
                if not cards:
                    consecutive_empty += 1
                    if consecutive_empty >= 3:
                        print("   3 paginas vacias consecutivas, deteniendo.")
                        break
                else:
                    consecutive_empty = 0
 
            else:
                # Terminamos el grupo → siguiente iteración del while intentará avanzar
                continue
            break   # salimos si hubo 3 vacias
 
        browser.close()
 
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
    from discord import app_commands
    from discord.ext import tasks
except ImportError:
    sys.exit("❌  pip install discord.py")
 
 
def _project_embed(p: dict, color: int = 0x4651E2) -> discord.Embed:
    e = discord.Embed(title=p["title"], url=p["url"] or None, color=color)
    if p["team"]:
        e.add_field(name="👥 Equipo",        value=p["team"],     inline=True)
    if p["category"]:
        short_cat = p["category"].split(" - ")[0] if " - " in p["category"] else p["category"]
        e.add_field(name="🏷️ Categoría",    value=short_cat,     inline=True)
    if p["description"]:
        desc = p["description"][:300] + ("…" if len(p["description"]) > 300 else "")
        e.add_field(name="📝 Descripción",   value=desc,          inline=False)
    if p["members"]:
        e.add_field(name="🙋 Participantes", value=p["members"],  inline=False)
    if p["url"]:
        e.add_field(name="🔗",               value=f"[Ver equipo]({p['url']})", inline=False)
    e.set_footer(text=f"ID: {p['id']} • Visto por primera vez: {p.get('first_seen','?')}")
    return e
 
 
async def send_project_list(
    interaction: discord.Interaction,
    projects: list[dict],
    title: str,
    color: int = 0x4651E2,
    empty_msg: str = "No se encontraron proyectos.",
):
    """Envía hasta 10 proyectos como embeds paginados en el canal."""
    await interaction.response.defer(ephemeral=False)
 
    if not projects:
        await interaction.followup.send(
            embed=discord.Embed(title=title, description=f"😕 {empty_msg}", color=0x95A5A6)
        )
        return
 
    MAX = 10
    header = discord.Embed(
        title=title,
        description=f"**{len(projects)}** proyecto(s) encontrado(s)"
                    + (f" — mostrando los primeros {MAX}." if len(projects) > MAX else "."),
        color=color,
    )
    await interaction.followup.send(embed=header)
 
    for p in projects[:MAX]:
        await interaction.channel.send(embed=_project_embed(p, color))
        await asyncio.sleep(0.4)
 
 
class GeniusArenaBot(discord.Client):
 
    def __init__(self):
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
 
    async def setup_hook(self):
        """Registra todos los slash commands y sincroniza al guild de forma instantánea."""
        self._register_commands()
        guild = discord.Object(id=GUILD_ID)
        # Copia los comandos globales al guild → aparecen de inmediato (sin esperar 1h)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        print(f"✅  Slash commands sincronizados al guild {GUILD_ID} (instantáneo)")
 
    def _register_commands(self):
 
        # ── /buscar ───────────────────────────────────────────────────────────
        @self.tree.command(name="buscar", description="Busca proyectos en la base de datos")
        @app_commands.describe(termino="Palabra clave: nombre, equipo, descripción o participante")
        async def cmd_buscar(interaction: discord.Interaction, termino: str):
            results = db_search(termino)
            await send_project_list(
                interaction,
                results,
                title=f"🔍 Búsqueda: \"{termino}\"",
                color=0x4651E2,
                empty_msg=f"Ningún proyecto coincide con **{termino}**.",
            )
 
        # ── /summary ──────────────────────────────────────────────────────────
        @self.tree.command(name="summary", description="Cuántos proyectos hay por categoría")
        async def cmd_summary(interaction: discord.Interaction):
            rows, total = db_summary()
            if total == 0:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="📊 Resumen por categoría",
                        description="La base de datos está vacía. Espera el primer scrape.",
                        color=0x95A5A6,
                    )
                )
                return
 
            e = discord.Embed(
                title="📊 Resumen por categoría",
                description=f"**{total}** proyectos registrados en total",
                color=0x4651E2,
                timestamp=datetime.now(),
            )
 
            # Asigna emoji si la categoría coincide con alguna conocida
            for cat, count in rows:
                emoji = "📁"
                for key, keyword in CATEGORIES.items():
                    if keyword.lower() in (cat or "").lower():
                        emoji = CAT_EMOJI[key]
                        break
                short = cat.split(" - ")[0] if cat and " - " in cat else (cat or "Sin categoría")
                bar   = "█" * min(count, 20)
                e.add_field(
                    name=f"{emoji} {short}",
                    value=f"`{bar}` **{count}**",
                    inline=False,
                )
 
            e.set_footer(text="Genius Arena Scraper")
            await interaction.response.send_message(embed=e)
 
        # ── Comandos por categoría (uno por empresa) ────────────────────────────
        for cmd_name, keyword in CATEGORIES.items():
            def make_cmd(cname, kw):
                _emoji = CAT_EMOJI[cname]
                _color = CAT_COLOR[cname]
                _label = kw.split(" - ")[0] if " - " in kw else kw
                _kw    = kw
 
                @self.tree.command(
                    name=cname,
                    description=f"{_emoji} Proyectos de {_label}"
                )
                async def _cmd(interaction: discord.Interaction):
                    results = db_by_category(_kw)
                    await send_project_list(
                        interaction,
                        results,
                        title=f"{_emoji} Proyectos — {_label}",
                        color=_color,
                        empty_msg=f"No hay proyectos de **{_label}** aún.",
                    )
 
            make_cmd(cmd_name, keyword)
 
    async def on_ready(self):
        print(f"✅  Bot conectado como {self.user}")
        print(f"📡  Canal: {CHANNEL_ID}")
        print(f"⏱   Scrape cada {CHECK_EVERY_HOURS}h\n")
        ch = self.get_channel(CHANNEL_ID)
        if ch:
            await ch.send(embed=self._embed_startup())
        self.check_loop.start()
 
    def _embed_startup(self):
        cmds = "\n".join(
            f"{CAT_EMOJI[k]} `/{k}`" for k in CATEGORIES
        )
        e = discord.Embed(
            title="🚀 Genius Arena Scraper activo",
            description=(
                f"Monitoreando **Talent Hackathon 2026** · scrape cada **{CHECK_EVERY_HOURS}h**\n\n"
                f"**Comandos disponibles:**\n"
                f"🔍 `/buscar <término>` — búsqueda libre\n"
                f"📊 `/summary` — resumen por categoría\n"
                f"{cmds}"
            ),
            color=0x4651E2,
        )
        e.set_footer(text=f"Iniciado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        return e
 
    @tasks.loop(hours=CHECK_EVERY_HOURS)
    async def check_loop(self):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{now}] 🔍 Scraping …")
        ch   = self.get_channel(CHANNEL_ID)
        loop = asyncio.get_event_loop()
 
        try:
            all_projects = await loop.run_in_executor(None, scrape_sync)
        except Exception as exc:
            print(f"  ❌ {exc}")
            if ch:
                await ch.send(embed=discord.Embed(
                    title="⚠️ Error en el scraper",
                    description=f"```{exc}```",
                    color=0xFF4444,
                ))
            return
 
        conn = get_conn()
        prev_ids = get_seen_ids(conn)
        new_projects  = [p for p in all_projects if p["id"] not in prev_ids]
        seen_projects = [p for p in all_projects if p["id"] in     prev_ids]
 
        save_new_projects(conn, new_projects, now)
        json_file = save_run_json(now, new_projects, seen_projects)
        log_run(conn, now, len(all_projects), len(new_projects), json_file)
        conn.close()
 
        print(f"  Total: {len(all_projects)} | Nuevos: {len(new_projects)}")
 
        if not ch:
            return
 
        if new_projects:
            await ch.send(embed=discord.Embed(
                title=f"🆕 {len(new_projects)} proyecto(s) nuevo(s)",
                description=f"**{len(all_projects)}** en total · **{len(new_projects)}** nuevos",
                color=0x2ECC71,
                timestamp=datetime.now(),
            ))
            for p in new_projects:
                await ch.send(embed=_project_embed(p))
                await asyncio.sleep(0.5)
        else:
            await ch.send(embed=discord.Embed(
                title="✅ Sin proyectos nuevos",
                description=f"Se revisaron **{len(all_projects)}** proyectos — sin novedades.",
                color=0x95A5A6,
                timestamp=datetime.now(),
            ).set_footer(text=f"Próxima revisión en {CHECK_EVERY_HOURS}h"))
 
    @check_loop.before_loop
    async def before_check(self):
        await self.wait_until_ready()
 
 
# ── Entrypoint ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    validate_config()
    conn = get_conn()
    init_db(conn)
    conn.close()
    bot = GeniusArenaBot()
    bot.run(DISCORD_TOKEN)
