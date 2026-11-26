# -*- coding: utf-8 -*-
import asyncio, json, logging, os, sys
import discord
from discord.ext import commands

import backup
from backup import backup_loop

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
    force=True,
)
log = logging.getLogger(__name__)


def _load_token():
    token = os.environ.get("DISCORD_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
    if token:
        return token.strip()
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return str(json.load(f).get("token", "")).strip()
        except Exception:
            pass
    print("Токен не найден. Задай DISCORD_BOT_TOKEN или config.json")
    sys.exit(1)


intents = discord.Intents.default()
intents.voice_states = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Синк команд — только один раз за запуск: при переподключении к гейтвею
# on_ready срабатывает снова, а лимиты на синк у Discord жёсткие.
_synced_once = False
_backup_started = False


@bot.event
async def on_ready():
    global _synced_once, _backup_started
    if not _backup_started:
        _backup_started = True
        bot.loop.create_task(backup_loop(bot))
        log.info(
            "Авто-бэкап data/ запущен: каждые %s ч., храним %s",
            backup.INTERVAL_HOURS,
            backup.KEEP,
        )
    guild_id = int(os.environ.get("GUILD_ID", "0"))
    if not guild_id and os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                guild_id = int(json.load(f).get("guild_id", 0))
        except Exception:
            pass
    if not guild_id:
        guild_id = 1573386089467748482
    if _synced_once:
        return
    guild = discord.Object(id=guild_id)
    bot.tree.copy_global_to(guild=guild)
    synced = await bot.tree.sync(guild=guild)
    log.info("Rooms-бот запущен: %s | Синкнуто %d команд на гильдию %d", bot.user, len(synced), guild_id)
    _synced_once = True


async def main():
    async with bot:
        try:
            await bot.load_extension("cogs.rooms")
            log.info("Cog rooms загружен")
        except Exception as e:
            log.error("ОШИБКА загрузки cog: %s", e)
            raise
        try:
            await bot.load_extension("cogs.rp")
            log.info("Cog rp загружен")
        except Exception as e:
            log.error("ОШИБКА загрузки cog rp: %s", e)
        await bot.start(_load_token())


if __name__ == "__main__":
    asyncio.run(main())
