import os, sys, asyncio, logging

import discord
from discord.ext import commands, tasks

# ══════════════════════════════════════════════════════════════════════
#  KEEP-ALIVE SERVER  (Render free tier — ping this URL with UptimeRobot)
# ══════════════════════════════════════════════════════════════════════

async def _keep_alive_server():
    async def _handle(reader, writer):
        try:
            await reader.read(2048)
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/plain\r\n"
                b"Content-Length: 21\r\n"
                b"Connection: close\r\n"
                b"\r\n"
                b"Nezuko is protecting!"
            )
            await writer.drain()
        except Exception:
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
    port = int(os.environ.get("PORT", 10000))
    server = await asyncio.start_server(_handle, "0.0.0.0", port)
    logger.info(f"[Keep-alive] Listening on port {port}")
    async with server:
        await server.serve_forever()

# ══════════════════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("nezuko-bot")

# ══════════════════════════════════════════════════════════════════════
#  ENVIRONMENT VARIABLES  (set these in Render dashboard)
# ══════════════════════════════════════════════════════════════════════

TOKEN = os.getenv("TOKEN", "")

_VC_IDS_RAW = os.getenv("VC_IDS", "")
VC_IDS = [int(x.strip()) for x in _VC_IDS_RAW.split(",") if x.strip().isdigit()] if _VC_IDS_RAW.strip() else []

if not VC_IDS:
    logger.warning("[VC] VC_IDS not set — voice channel features disabled.")

# ══════════════════════════════════════════════════════════════════════
#  STATUS  (fixed — always shows this)
# ══════════════════════════════════════════════════════════════════════

STATUS_TEXT = "ABN ON TOP 🔥"

# ══════════════════════════════════════════════════════════════════════
#  BOT SETUP
# ══════════════════════════════════════════════════════════════════════

intents = discord.Intents.default()
intents.voice_states = True

bot = commands.Bot(command_prefix="nezuko ", intents=intents, help_command=None)

# ══════════════════════════════════════════════════════════════════════
#  VC MOVE LOGIC  (join + follow between monitored voice channels)
# ══════════════════════════════════════════════════════════════════════

def _vc_has_users(vc: discord.VoiceChannel) -> bool:
    return any(m for m in vc.members if not m.bot)

async def _move_bot(guild: discord.Guild, go_to: discord.VoiceChannel = None):
    """
    Priority order:
      1. go_to given → follow that user immediately.
      2. Already in the correct VC → do nothing.
      3. Find any monitored VC with users → move there.
      4. Nobody anywhere → stay put (never disconnect).
      5. Not connected at all → join first valid VC_IDS channel.
    """
    if not VC_IDS:
        return

    vc_client = guild.voice_client

    # ── Step 1: hard follow ──────────────────────────────────────────
    if go_to and go_to.id in VC_IDS:
        if vc_client and vc_client.is_connected():
            if vc_client.channel.id == go_to.id:
                return
            try:
                await vc_client.move_to(go_to)
            except Exception as e:
                logger.warning(f"[VC] move_to {go_to.name} failed: {e}")
        else:
            try:
                await go_to.connect()
            except Exception as e:
                logger.warning(f"[VC] connect to {go_to.name} failed: {e}")
        return

    # ── Step 2: find best populated VC ──────────────────────────────
    best = None
    for vc_id in VC_IDS:
        vc = guild.get_channel(vc_id)
        if vc and isinstance(vc, discord.VoiceChannel) and _vc_has_users(vc):
            best = vc
            break

    if best:
        if vc_client and vc_client.is_connected():
            if vc_client.channel.id == best.id:
                return
            try:
                await vc_client.move_to(best)
            except Exception as e:
                logger.warning(f"[VC] move_to {best.name} failed: {e}")
        else:
            try:
                await best.connect()
            except Exception as e:
                logger.warning(f"[VC] connect to {best.name} failed: {e}")
        return

    # ── Step 3: nobody anywhere ──────────────────────────────────────
    if vc_client and vc_client.is_connected():
        return  # stay wherever we are

    for vc_id in VC_IDS:
        vc = guild.get_channel(vc_id)
        if vc and isinstance(vc, discord.VoiceChannel):
            try:
                await vc.connect()
                return
            except Exception as e:
                logger.warning(f"[VC] startup connect to {vc.name} failed: {e}")

# ══════════════════════════════════════════════════════════════════════
#  BACKGROUND TASKS
# ══════════════════════════════════════════════════════════════════════

@tasks.loop(seconds=300)
async def vc_reconnect_heartbeat():
    """Every 5 min: if bot got kicked / disconnected, rejoin."""
    for guild in bot.guilds:
        try:
            vc_client = guild.voice_client
            if vc_client and vc_client.is_connected():
                continue
            await _move_bot(guild)
        except Exception as e:
            logger.warning(f"[VC heartbeat] {e}")

# ══════════════════════════════════════════════════════════════════════
#  EVENTS
# ══════════════════════════════════════════════════════════════════════

@bot.event
async def on_ready():
    logger.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")

    # Set fixed presence
    try:
        await bot.change_presence(
            activity=discord.Game(name=STATUS_TEXT),
            status=discord.Status.online,
        )
    except Exception:
        pass

    # Connect to voice channels on startup
    for guild in bot.guilds:
        try:
            await _move_bot(guild)
        except Exception:
            pass

    # Start background tasks
    if not vc_reconnect_heartbeat.is_running():
        vc_reconnect_heartbeat.start()

    # Start keep-alive HTTP server
    asyncio.create_task(_keep_alive_server())

    logger.info("🌸 Nezuko Bot is ready!")

@bot.event
async def on_voice_state_update(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    if member.id == bot.user.id:
        return

    guild = member.guild

    was_monitored = before.channel is not None and before.channel.id in VC_IDS
    now_monitored = after.channel  is not None and after.channel.id  in VC_IDS

    if now_monitored and (not was_monitored or before.channel.id != after.channel.id):
        await _move_bot(guild, go_to=after.channel)
    elif was_monitored and not now_monitored:
        await _move_bot(guild)
    elif was_monitored and now_monitored and before.channel.id != after.channel.id:
        await _move_bot(guild, go_to=after.channel)

# ══════════════════════════════════════════════════════════════════════
#  RUN
# ══════════════════════════════════════════════════════════════════════

if not TOKEN:
    logger.error("TOKEN env var is not set!")
    sys.exit(1)

bot.run(TOKEN)
