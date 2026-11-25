# -*- coding: utf-8 -*-
"""РП-команды для пар: обнять, поцеловать, погладить, приобнять, угостить,
помахать, чмок.

Картинки: сначала ищем локальный файл в assets/rp/ (если положишь свои гифки —
бот возьмёт их), иначе тянем гифку из бесплатного nekos.best. Если и он молчит,
команда всё равно сработает — просто текстом, без картинки.

Если оба собеседника женаты друг на друге, эмбед становится розовым
и добавляется строчка про вторую половинку.
"""

import logging
import os
import random
import time

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RP_DIR = os.path.join(BASE_DIR, "assets", "rp")

HEART_COLOR = 0xFF9AD5
CALM_COLOR = 0xA9C9FF
GIFT_COLOR = 0xF0B46A

NEKOS_API = "https://nekos.best/api/v2/{endpoint}"
USER_AGENT = "RoetDeluxe/1.0 (+https://github.com/funpaybots/bot-rooms)"

# Чистим за собой: РП-сообщения удаляются через 5 минут, чтобы не копился спам.
DELETE_AFTER = 300
# Общая пауза между любыми РП-действиями одного человека, чтобы не прыгали
# с /обнять на /поцеловать каждую секунду. 0 — выключить.
GLOBAL_COOLDOWN = 10.0

# ключ команды → (файл, эндпоинт nekos.best, заголовок, цвет, варианты текста)
# {a} — кто действует, {b} — на кого
ACTIONS = {
    "обнять": (
        "hug.gif", "hug", "🤗 Обнимашки", CALM_COLOR,
        (
            "{a} крепко обнимает {b}",
            "{a} прижимает {b} к себе",
            "{a} дарит {b} тёплые объятия",
        ),
    ),
    "поцеловать": (
        "kiss.gif", "kiss", "😽 Поцелуй", HEART_COLOR,
        (
            "{a} целует {b}",
            "{a} дарит {b} поцелуй",
            "{a} нежно целует {b}",
        ),
    ),
    "погладить": (
        "pat.gif", "pat", "🫶 Погладить", CALM_COLOR,
        (
            "{a} гладит {b} по голове",
            "{a} ласково треплет {b} по волосам",
            "{a} чешет {b} за ушком",
        ),
    ),
    "приобнять": (
        "cuddle.gif", "cuddle", "🫂 Приобнять", HEART_COLOR,
        (
            "{a} укутывает {b} в плед и прижимается рядом",
            "{a} усаживает {b} рядом и приобнимает",
            "{a} делится с {b} теплом пледа",
        ),
    ),
    "угостить": (
        "feed.gif", "feed", "🍰 Угостить", GIFT_COLOR,
        (
            "{a} угощает {b} чем-то вкусным",
            "{a} протягивает {b} кусочек тортика",
            "{a} кормит {b} вкусняшкой",
        ),
    ),
    "помахать": (
        "wave.gif", "wave", "👋 Привет", CALM_COLOR,
        (
            "{a} машет {b} рукой",
            "{a} приветливо машет {b}",
            "{a} машет {b}, заходя в комнату",
        ),
    ),
    "чмок": (
        "blowkiss.gif", "blowkiss", "😘 Воздушный поцелуй", HEART_COLOR,
        (
            "{a} посылает {b} воздушный поцелуй",
            "{a} шлёт {b} чмок издалека",
            "{a} ловит и отправляет {b} воздушный поцелуй",
        ),
    ),
}

SPOUSE_LINES = (
    "💞 Вторая половинка — самые тёплые объятия",
    "💞 Вы ведь супруги, у вас всё по-настоящему",
    "❤️ Это ваша вторая половинка",
)

SELF_LINES = (
    "Сам себя не обнимешь… хотя почему бы и нет. Выбери кого-то другого.",
    "Себя любить тоже важно, но обними кого-нибудь ещё.",
    "Самообнимашки — это святое, но давай кого-то другого.",
)

BOT_LINES = (
    "Боты железные — объятия не заходят.",
    "Бот не оценит, но спасибо за доброту.",
    "Мне приятно, но у меня нет чувств. Попробуй с человеком.",
)


def find_local_image(filename: str) -> str:
    """Путь к локальной картинке в assets/rp/ или пустая строка.

    Ищем по имени без расширения: hug.gif, hug.png, hug.webp — что найдётся.
    """
    stem = os.path.splitext(filename)[0]
    for ext in (".gif", ".png", ".webp", ".jpg", ".jpeg"):
        path = os.path.join(RP_DIR, stem + ext)
        if os.path.isfile(path):
            return path
    return ""


def build_embed(
    action: str, actor_name: str, target_name: str, *, spouse: bool = False,
) -> tuple:
    """Чистая сборка эмбеда: (embed, имя локального файла).

    Картинку навешивает вызывающий код: локальный файл или ссылку из API.
    """
    filename, _endpoint, title, color, variants = ACTIONS[action]
    text = random.choice(variants).format(a=actor_name, b=target_name)
    embed = discord.Embed(
        title=title,
        description=text,
        color=HEART_COLOR if spouse else color,
    )
    if spouse:
        embed.description += "\n\n" + random.choice(SPOUSE_LINES)
    embed.set_footer(text="❤️ Roet Deluxe")
    return embed, filename


def parse_nekos_payload(data) -> str:
    """Вытащить ссылку на гифку из ответа nekos.best. Пустая строка — не подходит."""
    results = data.get("results") if isinstance(data, dict) else None
    if not results or not isinstance(results, list):
        return ""
    first = results[0] if isinstance(results[0], dict) else {}
    link = str(first.get("url") or "")
    return link if link.startswith("https://") else ""


async def fetch_image_url(action: str) -> str:
    """Ссылка на гифку из nekos.best. Пустая строка — не достали, живём без картинки."""
    endpoint = ACTIONS[action][1]
    url = NEKOS_API.format(endpoint=endpoint)
    try:
        timeout = aiohttp.ClientTimeout(total=4)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url, headers={"User-Agent": USER_AGENT}) as resp:
                if resp.status != 200:
                    return ""
                data = await resp.json()
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return ""
    return parse_nekos_payload(data)


class RP(commands.Cog):
    """РП-команды. Кулдаун 3 секунды на человека, чтобы не спамили."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # (guild_id, user_id) → время последнего РП-действия
        self._last_use = {}

    async def _rate_ok(self, interaction: discord.Interaction) -> bool:
        """Общий тормоз между РП-командами. False — уже ответили отказом."""
        if GLOBAL_COOLDOWN <= 0:
            return True
        key = (interaction.guild_id, interaction.user.id)
        now = time.monotonic()
        last = self._last_use.get(key)
        if last is not None and now - last < GLOBAL_COOLDOWN:
            left = GLOBAL_COOLDOWN - (now - last)
            await interaction.response.send_message(
                f"РП-пауза: ещё {left:.0f} с.", ephemeral=True
            )
            return False
        self._last_use[key] = now
        return True

    @staticmethod
    def _is_spouse(guild, a_id: int, b_id: int) -> bool:
        if guild is None:
            return False
        try:
            from cogs import rooms as rooms_cog

            partner, _ = rooms_cog._alive_partner(guild, a_id)
        except Exception:  # noqa: BLE001 - романтика не должна падать из-за брака
            return False
        return bool(partner and partner.id == b_id)

    async def _run(self, interaction: discord.Interaction, action: str, target: discord.Member):
        if getattr(target, "bot", False):
            await interaction.response.send_message(random.choice(BOT_LINES), ephemeral=True)
            return
        if target.id == interaction.user.id:
            await interaction.response.send_message(random.choice(SELF_LINES), ephemeral=True)
            return

        if not await self._rate_ok(interaction):
            return

        spouse = self._is_spouse(interaction.guild, interaction.user.id, target.id)
        embed, filename = build_embed(
            action,
            interaction.user.display_name,
            target.display_name,
            spouse=spouse,
        )

        path = find_local_image(filename)
        if path:
            name = os.path.basename(path)
            embed.set_image(url=f"attachment://{name}")
            await interaction.response.send_message(
                embed=embed,
                file=discord.File(path, filename=name),
                delete_after=DELETE_AFTER,
            )
            return

        link = await fetch_image_url(action)
        if link:
            embed.set_image(url=link)
        await interaction.response.send_message(embed=embed, delete_after=DELETE_AFTER)

    # ── ошибки ────────────────────────────────────────────────────────────────

    async def cog_app_command_error(self, interaction: discord.Interaction, error):
        """Кулдаун и прочие сбои — понятным текстом, а не «приложение не отвечает»."""
        if isinstance(error, app_commands.CommandOnCooldown):
            text = f"Полегче, объятия подождут {error.retry_after:.0f} с."
        else:
            text = "Что-то пошло не так. Попробуй ещё раз."
            log.error("Ошибка РП-команды: %s", error)
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass

    # ── команды ───────────────────────────────────────────────────────────────

    @app_commands.command(name="обнять", description="Обнять кого-то 🤗")
    @app_commands.describe(user="Кого обнять")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def hug(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "обнять", user)

    @app_commands.command(name="поцеловать", description="Поцеловать кого-то 😽")
    @app_commands.describe(user="Кого поцеловать")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def kiss(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "поцеловать", user)

    @app_commands.command(name="погладить", description="Погладить кого-то по голове 🫶")
    @app_commands.describe(user="Кого погладить")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def pat(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "погладить", user)

    @app_commands.command(name="приобнять", description="Укутать кого-то в плед 🫂")
    @app_commands.describe(user="Кого приобнять")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def cuddle(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "приобнять", user)

    @app_commands.command(name="угостить", description="Угостить кого-то вкусным 🍰")
    @app_commands.describe(user="Кого угостить")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def feed(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "угостить", user)

    @app_commands.command(name="помахать", description="Помахать кому-то рукой 👋")
    @app_commands.describe(user="Кому помахать")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def wave(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "помахать", user)

    @app_commands.command(name="чмок", description="Послать воздушный поцелуй 😘")
    @app_commands.describe(user="Кому послать")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def blowkiss(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "чмок", user)


async def setup(bot: commands.Bot):
    await bot.add_cog(RP(bot))
