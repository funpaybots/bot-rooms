# -*- coding: utf-8 -*-
"""РП-команды: обнять, поцеловать, погладить и ещё полтора десятка действий.

Картинки: сначала ищем локальный файл в assets/rp/ (если положишь свои гифки —
бот возьмёт их), иначе тянем гифку из бесплатного nekos.best. Если и он молчит,
команда всё равно сработает — просто текстом, без картинки.

Под каждым сообщением есть кнопка «Ответить взаимностью» — её может нажать
только получатель. Сообщения удаляются через 5 минут, есть общая РП-пауза.
Модерация может выключить РП в конкретном канале (/рп-выкл, /рп-вкл).

Если оба собеседника женаты друг на друге, эмбед становится розовым
и добавляется комплимент с упоминанием второй половинки.
"""

import logging
import os
import random
import time

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from storage import load, save

log = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RP_DIR = os.path.join(BASE_DIR, "assets", "rp")

HEART_COLOR = 0xFF9AD5
CALM_COLOR = 0xA9C9FF
GIFT_COLOR = 0xF0B46A
PLAY_COLOR = 0xFFB86B

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
    "пнуть": (
        "kick.gif", "kick", "🦵 Пинок", PLAY_COLOR,
        (
            "{a} легонько пинает {b}",
            "{a} отвешивает {b} дружеский пинок",
            "{a} пинает {b} по-дружески",
        ),
    ),
    "шлёпнуть": (
        "slap.gif", "slap", "🖐 Шлёп", PLAY_COLOR,
        (
            "{a} шутливо шлёпает {b}",
            "{a} отвешивает {b} лёгкий шлепок",
            "{a} шлёпает {b} по плечу",
        ),
    ),
    "стукнуть": (
        "bonk.gif", "bonk", "🔨 Бонк", PLAY_COLOR,
        (
            "{a} стукает {b} по голове",
            "{a} отвешивает {b} лёгкий бонк",
            "{a} стукает {b} кулачком",
        ),
    ),
    "укусить": (
        "bite.gif", "bite", "😼 Кусь", PLAY_COLOR,
        (
            "{a} кусает {b}",
            "{a} шутливо кусает {b} за плечо",
            "{a} цапает {b} за руку",
        ),
    ),
    "ткнуть": (
        "poke.gif", "poke", "👉 Тык", PLAY_COLOR,
        (
            "{a} тыкает {b} в бок",
            "{a} тычет в {b} пальцем",
            "{a} тыкает {b} и хихикает",
        ),
    ),
    "пощекотать": (
        "tickle.gif", "tickle", "🤭 Щекотка", PLAY_COLOR,
        (
            "{a} щекочет {b}",
            "{a} нападает на {b} с щекоткой",
            "{a} щекочет {b} до слёз",
        ),
    ),
    "пять": (
        "highfive.gif", "highfive", "🙌 Пять", CALM_COLOR,
        (
            "{a} даёт {b} пять",
            "{a} ловит ладонь {b} для хай-файва",
            "{a} хлопает {b} по ладони",
        ),
    ),
    "держаться": (
        "handhold.gif", "handhold", "🤝 За руку", HEART_COLOR,
        (
            "{a} берёт {b} за руку",
            "{a} переплетает пальцы с {b}",
            "{a} не отпускает руку {b}",
        ),
    ),
    "унести": (
        "carry.gif", "carry", "🫳 На руках", HEART_COLOR,
        (
            "{a} берёт {b} на руки",
            "{a} уносит {b} в закат",
            "{a} несёт {b} на руках",
        ),
    ),
    "прижать": (
        "kabedon.gif", "kabedon", "🧱 Прижать", HEART_COLOR,
        (
            "{a} прижимает {b} к стене",
            "{a} загоняет {b} в угол",
            "{a} припирает {b} к стене и улыбается",
        ),
    ),
    "дразнить": (
        "baka.gif", "baka", "😝 Дразнить", PLAY_COLOR,
        (
            "{a} дразнит {b}",
            "{a} показывает {b} язык",
            "{a} обзывает {b} бака и хихикает",
        ),
    ),
    "подмигнуть": (
        "wink.gif", "wink", "😉 Подмигнуть", CALM_COLOR,
        (
            "{a} подмигивает {b}",
            "{a} игриво подмигивает {b}",
            "{a} шлёт {b} подмигивание",
        ),
    ),
    "танцевать": (
        "dance.gif", "dance", "💃 Танец", CALM_COLOR,
        (
            "{a} приглашает {b} на танец",
            "{a} танцует с {b}",
            "{a} кружит {b} в танце",
        ),
    ),
}

# Строчки для супругов: комплимент второй половинке с упоминанием @.
# {a} — имя того, кто действует, {b} — упоминание того, на кого направлено.
SPOUSE_LINES = (
    "💞 {b}, ты — самое тёплое, что есть у {a}",
    "❤️ {b}, с тобой у {a} всё по-настоящему",
    "💞 {b}, {a} ни на кого тебя не променяет",
    "❤️ {b}, ты лучшее, что случилось с {a}",
)

# Для пинков и щекотки романтичная строчка выглядит странно — своя.
PLAYFUL = {
    "пнуть", "шлёпнуть", "стукнуть", "укусить", "ткнуть", "пощекотать", "дразнить",
}

PLAYFUL_SPOUSE_LINES = (
    "💞 {b}, {a} пинает только самых любимых",
    "💞 {b}, ты у {a} любимый человек даже после пинка",
    "❤️ {b}, {a} всё равно тебя обожает",
)

# Ответное действие для кнопки «в ответ»: глагол в 3-м лице.
REPLY_VERBS = {
    "обнять": "обнимает",
    "поцеловать": "целует",
    "погладить": "гладит",
    "приобнять": "приобнимает",
    "угостить": "угощает",
    "помахать": "машет",
    "чмок": "чмокает",
    "пнуть": "пинает",
    "шлёпнуть": "шлёпает",
    "стукнуть": "стукает",
    "укусить": "кусает",
    "ткнуть": "тыкает",
    "пощекотать": "щекочет",
    "пять": "даёт пять",
    "держаться": "берёт за руку",
    "унести": "уносит",
    "прижать": "прижимает",
    "дразнить": "дразнит",
    "подмигнуть": "подмигивает",
    "танцевать": "приглашает на танец",
}

# ── каналы, где РП выключены (модерация) ─────────────────────────────────────
RP_CHANNELS_FILE = "rp_channels.json"


def disabled_channels(guild_id: int) -> list:
    data = load(RP_CHANNELS_FILE, {})
    return list(data.get(str(guild_id)) or [])


def is_disabled(guild_id: int, channel_id: int) -> bool:
    return channel_id in disabled_channels(guild_id)


def set_disabled(guild_id: int, channel_id: int, disabled: bool) -> list:
    """Включить/выключить РП в канале. Возвращает актуальный список."""
    data = load(RP_CHANNELS_FILE, {})
    current = list(data.get(str(guild_id)) or [])
    if disabled and channel_id not in current:
        current.append(channel_id)
    elif not disabled and channel_id in current:
        current.remove(channel_id)
    if current:
        data[str(guild_id)] = current
    else:
        data.pop(str(guild_id), None)
    save(RP_CHANNELS_FILE, data)
    return current

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


def _gender(member) -> str:
    """'m' или 'f' по ролям пола сервера, пусто — не поняли."""
    try:
        from cogs.rooms import MALE_ROLE_ID, FEMALE_ROLE_ID
    except Exception:  # noqa: BLE001 - без rooms живём без пола
        return ""
    ids = {r.id for r in (getattr(member, "roles", None) or [])}
    if MALE_ROLE_ID in ids:
        return "m"
    if FEMALE_ROLE_ID in ids:
        return "f"
    return ""


def pair_suffix(actor, target) -> str:
    """'mm' / 'ff' / 'mf' для выбора картинки под пару. Пусто — пол неизвестен."""
    a, b = _gender(actor), _gender(target)
    if not a or not b:
        return ""
    return a * 2 if a == b else "mf"


def find_local_image(filename: str, suffix: str = "") -> str:
    """Путь к локальной картинке в assets/rp/ или пустая строка.

    Сначала ищем вариант под пару (hug_mm.gif), потом общий (hug.gif).
    Расширение любое: gif, png, webp, jpg.
    """
    stem = os.path.splitext(filename)[0]
    names = [f"{stem}_{suffix}"] if suffix else []
    names.append(stem)
    for name in names:
        for ext in (".gif", ".png", ".webp", ".jpg", ".jpeg"):
            path = os.path.join(RP_DIR, name + ext)
            if os.path.isfile(path):
                return path
    return ""


def build_embed(
    action: str,
    actor_name: str,
    target_name: str,
    *,
    spouse: bool = False,
    actor_mention: str = "",
    target_mention: str = "",
) -> tuple:
    """Чистая сборка эмбеда: (embed, имя локального файла).

    actor_mention/target_mention — «<@id>»: если переданы, в тексте будут
    живые упоминания вместо простых имён. Картинку навешивает вызывающий код.
    """
    filename, _endpoint, title, color, variants = ACTIONS[action]
    a = actor_mention or actor_name
    b = target_mention or target_name
    text = random.choice(variants).format(a=a, b=b)
    embed = discord.Embed(
        title=title,
        description=text,
        color=HEART_COLOR if spouse else color,
    )
    if spouse:
        lines = PLAYFUL_SPOUSE_LINES if action in PLAYFUL else SPOUSE_LINES
        embed.description += "\n\n" + random.choice(lines).format(a=a, b=b)
    embed.set_footer(text="❤️ Roet Deluxe")
    return embed, filename


def build_mutual_embed(action: str, base_description: str, actor_mention: str, target_mention: str):
    """Ответ «взаимно»: тот же эмбед + строчка про ответное действие."""
    verb = REPLY_VERBS.get(action, "отвечает взаимностью")
    line = f"💞 {target_mention} {verb} {actor_mention} в ответ"
    embed = discord.Embed(
        title=ACTIONS[action][2],
        description=f"{base_description}\n\n{line}",
        color=HEART_COLOR,
    )
    embed.set_footer(text="❤️ Roet Deluxe")
    return embed


class ReplyView(discord.ui.View):
    """Кнопка «Ответить взаимностью» — нажать может только получатель."""

    def __init__(self, action: str, actor, target):
        super().__init__(timeout=None)  # живёт, пока живо сообщение
        self.action = action
        self.actor = actor
        self.target = target

    @discord.ui.button(label="Ответить взаимностью", style=discord.ButtonStyle.primary, emoji="💞")
    async def reply(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            await interaction.response.send_message(
                "Кнопка не твоя, но порыв засчитан 🙂", ephemeral=True
            )
            return
        old = interaction.message.embeds[0] if interaction.message.embeds else None
        if old is None:
            await interaction.response.send_message("Эмбед уже уехал.", ephemeral=True)
            return
        embed = build_mutual_embed(
            self.action,
            old.description or "",
            self.actor.mention,
            self.target.mention,
        )
        if old.image and old.image.url:
            embed.set_image(url=old.image.url)
        await interaction.response.edit_message(
            embed=embed,
            view=None,
            allowed_mentions=discord.AllowedMentions(
                users=[self.actor, self.target], everyone=False
            ),
        )


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
        if interaction.guild_id is not None and interaction.channel_id is not None:
            if is_disabled(interaction.guild_id, interaction.channel_id):
                await interaction.response.send_message(
                    "В этом канале РП выключены — тут не обнимаются.", ephemeral=True
                )
                return

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
            actor_mention=interaction.user.mention,
            target_mention=target.mention,
        )
        # Пингуем только участников действия, — никаких @everyone и ролей.
        mentions = discord.AllowedMentions(
            users=[interaction.user, target], everyone=False
        )

        # Кнопка ответа — только для обычного (не эфемерного) сообщения.
        view = ReplyView(action, interaction.user, target)

        path = find_local_image(filename, pair_suffix(interaction.user, target))
        if path:
            name = os.path.basename(path)
            embed.set_image(url=f"attachment://{name}")
            await interaction.response.send_message(
                embed=embed,
                file=discord.File(path, filename=name),
                delete_after=DELETE_AFTER,
                allowed_mentions=mentions,
                view=view,
            )
            return

        link = await fetch_image_url(action)
        if link:
            embed.set_image(url=link)
        await interaction.response.send_message(
            embed=embed,
            delete_after=DELETE_AFTER,
            allowed_mentions=mentions,
            view=view,
        )

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

    # ── хулиганские ───────────────────────────────────────────────────────────

    @app_commands.command(name="пнуть", description="Легонько пнуть кого-то 🦵")
    @app_commands.describe(user="Кого пнуть")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def kick(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "пнуть", user)

    @app_commands.command(name="шлёпнуть", description="Шутливо шлёпнуть кого-то 🖐")
    @app_commands.describe(user="Кого шлёпнуть")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def slap(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "шлёпнуть", user)

    @app_commands.command(name="стукнуть", description="Стукнуть кого-то по голове 🔨")
    @app_commands.describe(user="Кого стукнуть")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def bonk(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "стукнуть", user)

    @app_commands.command(name="укусить", description="Шутливо укусить кого-то 😼")
    @app_commands.describe(user="Кого укусить")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def bite(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "укусить", user)

    @app_commands.command(name="ткнуть", description="Тыкнуть кого-то пальцем 👉")
    @app_commands.describe(user="Кого ткнуть")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def poke(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "ткнуть", user)

    @app_commands.command(name="пощекотать", description="Защекотать кого-то 🤭")
    @app_commands.describe(user="Кого пощекотать")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def tickle(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "пощекотать", user)

    @app_commands.command(name="пять", description="Дать пять 🙌")
    @app_commands.describe(user="Кому дать пять")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def highfive(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "пять", user)

    @app_commands.command(name="держаться", description="Взять кого-то за руку 🤝")
    @app_commands.describe(user="За чью руку взяться")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def handhold(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "держаться", user)

    # ── ещё немного романтики ─────────────────────────────────────────────────

    @app_commands.command(name="унести", description="Взять кого-то на руки 🫳")
    @app_commands.describe(user="Кого унести")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def carry(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "унести", user)

    @app_commands.command(name="прижать", description="Прижать кого-то к стене 🧱")
    @app_commands.describe(user="Кого прижать")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def kabedon(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "прижать", user)

    @app_commands.command(name="дразнить", description="Подразнить кого-то 😝")
    @app_commands.describe(user="Кого дразнить")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def tease(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "дразнить", user)

    @app_commands.command(name="подмигнуть", description="Подмигнуть кому-то 😉")
    @app_commands.describe(user="Кому подмигнуть")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def wink(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "подмигнуть", user)

    @app_commands.command(name="танцевать", description="Пригласить на танец 💃")
    @app_commands.describe(user="С кем танцевать")
    @app_commands.checks.cooldown(1, 3.0, key=lambda i: (i.guild_id, i.user.id))
    async def dance(self, interaction: discord.Interaction, user: discord.Member):
        await self._run(interaction, "танцевать", user)

    # ── выключение РП в канале (модерация) ────────────────────────────────────

    @app_commands.command(name="рп-выкл", description="Выключить РП-команды в канале 🔇")
    @app_commands.describe(channel="Канал (по умолчанию — текущий)")
    @app_commands.default_permissions(manage_channels=True)
    async def rp_off(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await self._toggle(interaction, channel, disable=True)

    @app_commands.command(name="рп-вкл", description="Включить РП-команды обратно 🔊")
    @app_commands.describe(channel="Канал (по умолчанию — текущий)")
    @app_commands.default_permissions(manage_channels=True)
    async def rp_on(self, interaction: discord.Interaction, channel: discord.TextChannel = None):
        await self._toggle(interaction, channel, disable=False)

    async def _toggle(self, interaction: discord.Interaction, channel, *, disable: bool):
        if interaction.guild is None:
            await interaction.response.send_message("Только на сервере.", ephemeral=True)
            return
        perms = getattr(interaction.user, "guild_permissions", None)
        if perms is not None and not perms.manage_channels:
            await interaction.response.send_message(
                "Нужны права «Управление каналами».", ephemeral=True
            )
            return
        target_ch = channel or interaction.channel
        if target_ch is None:
            await interaction.response.send_message("Не понял канал.", ephemeral=True)
            return
        set_disabled(interaction.guild_id, target_ch.id, disable)
        verb = "выключены" if disable else "включены"
        await interaction.response.send_message(
            f"РП-команды {verb} в {target_ch.mention}.", ephemeral=True
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(RP(bot))
