# 🎯 Genius Arena — Discord Bot Scraper

Monitorea nuevos proyectos del **Talent Hackathon 2026** en Genius Arena y notifica automáticamente a un canal de Discord cada 2 horas.

---

## 📁 Estructura del proyecto

```
genius-arena-scraper/
├── discord_bot.py           # Bot de Discord + loop de chequeo
├── genius_arena_scraper.py  # Lógica de scraping (Playwright)
├── requirements.txt         # Dependencias
├── .env                     # Variables de entorno (no subir a git)
├── .env.example             # Plantilla del .env
├── genius_arena.db          # Base de datos SQLite (se genera automáticamente)
└── runs/                    # JSONs por cada ejecución (se genera automáticamente)
    └── run_2026-03-11_10-00-00.json
```

---

## ⚙️ Instalación

### 1. Clona o descarga el proyecto

```bash
git clone https://github.com/tu-usuario/genius-arena-scraper.git
cd genius-arena-scraper
```

### 2. Crea y activa el entorno virtual

```bash
# Crear el venv
python3 -m venv venv

# Activar en Mac/Linux
source venv/bin/activate

# Activar en Windows
venv\Scripts\activate
```

### 3. Instala las dependencias

```bash
pip install -r requirements.txt
```

### 4. Instala el navegador de Playwright

```bash
playwright install chromium
```

---

## 🔐 Configuración

### 1. Crea el archivo `.env`

Copia la plantilla y rellena tus datos:

```bash
cp .env.example .env
```

Edita `.env`:

```env
DISCORD_TOKEN=tu_token_aqui
CHANNEL_ID=123456789012345678
```

### 2. Cómo obtener cada valor

**`DISCORD_TOKEN`**

1. Ve a https://discord.com/developers/applications
2. Selecciona tu aplicación → **Bot**
3. Clic en **Reset Token** → cópialo (solo se muestra una vez)
4. En esa misma página activa **Message Content Intent**

**`CHANNEL_ID`**

1. En Discord: **Ajustes → Avanzado → Modo desarrollador** ✅
2. Clic derecho en el canal donde quieres las notificaciones → **Copiar ID del canal**

### 3. Invita el bot a tu servidor

1. En el portal: **OAuth2 → URL Generator**
2. Marca scope: `bot`
3. Marca permisos: `Send Messages`, `Embed Links`, `View Channels`
4. Copia la URL generada → ábrela en el navegador → selecciona tu servidor

### 4. Si el canal es privado

1. Clic derecho en el canal → **Editar canal → Permisos**
2. Agrega el bot con los permisos **Ver canal** y **Enviar mensajes**

---

## 🚀 Ejecutar

```bash
# Asegúrate de tener el venv activo
source venv/bin/activate   # Mac/Linux
# venv\Scripts\activate    # Windows

python3 discord_bot.py
```

Al iniciar verás en la terminal:

```
✅  Bot conectado como TuBot#1234
📡  Canal objetivo: 123456789012345678
⏱   Checando cada 2h
```

Y el bot enviará un mensaje de confirmación al canal de Discord.

---

## 📬 Mensajes en Discord

| Evento            | Mensaje                                                   |
| ----------------- | --------------------------------------------------------- |
| Bot inicia        | Embed azul con URL del hackathon y frecuencia             |
| Proyectos nuevos  | Embed verde de resumen + un embed por cada proyecto nuevo |
| Sin novedades     | Embed gris discreto con el total revisado                 |
| Error de scraping | Embed rojo con descripción del error                      |

Cada embed de proyecto incluye: título, equipo, categoría, descripción, participantes y link directo.

---

## 🗃️ Datos generados

**`genius_arena.db`** — SQLite con dos tablas:

- `projects` — todos los proyectos vistos con su fecha de primera detección
- `runs` — historial de cada ejecución

**`runs/run_FECHA_HORA.json`** — un archivo por ejecución:

```json
{
  "run_timestamp": "2026-03-11 10:00:00",
  "summary": {
    "total_found": 42,
    "new_projects": 3,
    "already_seen": 39
  },
  "new_projects": [...],
  "already_seen_projects": [...]
}
```

---

## 🖥️ Correr en background (tu propia PC)

Para dejar el bot corriendo sin tener la terminal abierta:

```bash
# Instala pm2
npm install -g pm2

# Inicia el bot
pm2 start discord_bot.py --interpreter python3 --name genius-arena-bot

# Guarda el proceso para que sobreviva reinicios
pm2 save
pm2 startup
```

Comandos útiles de pm2:

```bash
pm2 status                   # ver estado
pm2 logs genius-arena-bot    # ver logs en tiempo real
pm2 stop genius-arena-bot    # detener
pm2 restart genius-arena-bot # reiniciar
```

---

## 🔧 Solución de problemas

**El bot no encuentra proyectos**

- Cambia `HEADLESS = True` a `HEADLESS = False` en `discord_bot.py` para ver el navegador en acción
- Verifica que el sitio no requiera login

**Error de permisos en Discord**

- Confirma que el bot fue invitado con los permisos correctos
- Si el canal es privado, agrégalo manualmente en los permisos del canal

**`playwright install` falla**

- Asegúrate de tener el venv activo antes de instalar
- En Linux puede requerir dependencias extra: `playwright install-deps chromium`

---

## 📄 .env.example

```env
DISCORD_TOKEN=
CHANNEL_ID=
```

> ⚠️ Nunca subas tu archivo `.env` real a GitHub. Agrega `.env` a tu `.gitignore`.
