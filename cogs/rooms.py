# -*- coding: utf-8 -*-
"""Личные голосовые каналы.

Человек заходит в «➕ Создать уголок» → автоматически создаётся личный канал
в той же категории с кнопками управления в текстовом канале-панели.
"""

import datetime
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

from storage import load, save

log = logging.getLogger(__name__)

ROOMS_FILE = "rooms.json"
SETTINGS_FILE = "settings.json"
MARRIAGES_FILE = "marriages.json"

# ID канала-триггера и категории на сервере Roet (можно переопределить через /setup_rooms)
DEFAULT_TRIGGER_CH = 1573423260513607771   # ➕ · Создать уголок
DEFAULT_CATEGORY   = 1573423253500985344   # 🏠˚ Свой уголок
DEFAULT_PANEL_CH   = 1573423257724395575   # 🔑--мой-уголок

COLOR = 0xA9C9FF
HEART_COLOR = 0xFF9AD5

# Роли пола сервера Roet — брак только ♂ + ♀
MALE_ROLE_ID = 1573422846485594193
FEMALE_ROLE_ID = 1573422843574886453

# (guild_id, user_id) → ts последнего предложения, анти-спам
_last_propose = {}


# ── Вспомогательные ───────────────────────────────────────────────────────────

def _rooms(guild_id: int) -> dict:
    """Словарь channel_id → info для гильдии."""
    data = load(ROOMS_FILE, {})
    return data.get(str(guild_id), {})


def _save_rooms(guild_id: int, rooms: dict):
    data = load(ROOMS_FILE, {})
    data[str(guild_id)] = rooms
    save(ROOMS_FILE, data)


def _cfg(guild_id: int) -> dict:
    data = load(SETTINGS_FILE, {})
    return data.get(str(guild_id), {})


def _save_cfg(guild_id: int, cfg: dict):
    data = load(SETTINGS_FILE, {})
    data[str(guild_id)] = cfg
    save(SETTINGS_FILE, data)


def _owner_of(guild_id: int, channel_id: int) -> int | None:
    rooms = _rooms(guild_id)
    info = rooms.get(str(channel_id))
    return info.get("owner_id") if info else None


# ── Свадьбы ───────────────────────────────────────────────────────────────────

def _marriages(guild_id: int) -> dict:
    return load(MARRIAGES_FILE, {}).get(str(guild_id), {})


def _save_marriages(guild_id: int, data: dict):
    all_data = load(MARRIAGES_FILE, {})
    all_data[str(guild_id)] = data
    save(MARRIAGES_FILE, all_data)


def _partner_of(guild_id: int, user_id: int):
    """(partner_id, since_ts) или None."""
    m = _marriages(guild_id).get(str(user_id))
    if not m:
        return None
    return int(m["partner_id"]), float(m.get("since_ts", 0))


def _set_marriage(guild_id: int, a: int, b: int, since_ts: float):
    data = _marriages(guild_id)
    data[str(a)] = {"partner_id": b, "since_ts": since_ts}
    data[str(b)] = {"partner_id": a, "since_ts": since_ts}
    _save_marriages(guild_id, data)


def _remove_marriage(guild_id: int, a: int, b: int):
    data = _marriages(guild_id)
    data.pop(str(a), None)
    data.pop(str(b), None)
    _save_marriages(guild_id, data)


def _days_together(since_ts: float) -> int:
    return max(0, int((time.time() - since_ts) // 86400))


# ── Плюшки для пар ────────────────────────────────────────────────────────────

MARRIAGE_ROLE_NAME = "💍 В браке"
DEFAULT_COUPLE_LIMIT = 2

# Юбилеи: дни вместе
MILESTONES = [7, 30, 100, 182] + [365 * k for k in range(1, 21)]


def _milestone_text(m: int) -> str:
    if m == 7:
        return "неделя вместе"
    if m == 30:
        return "месяц вместе"
    if m == 100:
        return "100 дней вместе"
    if m == 182:
        return "полгода вместе"
    if m % 365 == 0:
        years = m // 365
        word = "год" if years == 1 else ("года" if years in (2, 3, 4) else "лет")
        return f"{years} {word} вместе"
    return f"{m} дней вместе"


def _next_milestone(days: int):
    for m in MILESTONES:
        if m > days:
            return m
    return None


def _reached_milestones(days: int, done) -> list:
    """Все юбилеи, которые уже наступили, но ещё не отмечали."""
    done = {int(d) for d in (done or [])}
    return [m for m in MILESTONES if m <= days and m not in done]


def _couple_room_for(guild_id: int, a_id: int, b_id: int):
    """(voice_id, info) парной комнаты этой пары или (None, None)."""
    for ch_id, info in _rooms(guild_id).items():
        if info.get("kind") != "couple":
            continue
        owner = int(info.get("owner_id") or 0)
        partner = int(info.get("partner_id") or 0)
        if {owner, partner} == {int(a_id), int(b_id)}:
            return int(ch_id), info
    return None, None


# ── Приватность парных комнат ─────────────────────────────────────────────────

def _roles_to_hide(guild) -> list:
    """Все роли, которым нечего делать в комнате пары.

    Категория «Мурмир» обычно пускает стафф внутрь, и права категории
    наследуются любой ролью. Поэтому закрываем канал от ВСЕХ ролей, а не
    только от тех, у кого есть права модерации: «Комьюнити Менеджер» или
    «dev» иначе остаются с доступом. @everyone, роли ботов и нашу роль
    не трогаем — они и так не должны/не могут.
    """
    me = getattr(guild, "me", None)
    bot_roles = set(getattr(me, "roles", []) or [])
    roles = []
    for role in getattr(guild, "roles", []):
        if role.is_default() or getattr(role, "managed", False) or role in bot_roles:
            continue
        roles.append(role)
    return roles


async def _hide_from_staff(channel, guild, *, voice: bool) -> int:
    """Закрывает канал от всех ролей. Возвращает, скольким ролям закрыл."""
    hidden = 0
    for role in _roles_to_hide(guild):
        try:
            if voice:
                await channel.set_permissions(role, view_channel=False, connect=False)
            else:
                await channel.set_permissions(role, view_channel=False)
        except (discord.Forbidden, discord.HTTPException):
            continue
        hidden += 1
    return hidden


async def _hide_existing_couple_rooms(guild) -> int:
    """Прогнать приватность по уже созданным комнатам пар."""
    fixed = 0
    for ch_id, info in _rooms(guild.id).items():
        if info.get("kind") != "couple":
            continue
        try:
            vc = guild.get_channel(int(ch_id))
        except (TypeError, ValueError):
            continue
        if vc is not None:
            await _hide_from_staff(vc, guild, voice=True)
            fixed += 1
        t_id = int(info.get("text_channel_id") or 0)
        text_ch = guild.get_channel(t_id) if t_id else None
        if text_ch is not None:
            await _hide_from_staff(text_ch, guild, voice=False)
    return fixed


async def _ensure_marriage_role(guild, role_id=None):
    """Роль «💍 В браке»: берём настроенную, ищем по имени или создаём."""
    role = guild.get_role(int(role_id)) if role_id else None
    if role is not None:
        return role
    role = discord.utils.get(getattr(guild, "roles", []), name=MARRIAGE_ROLE_NAME)
    if role is not None:
        cfg = _cfg(guild.id)
        cfg["marriage_role_id"] = role.id
        _save_cfg(guild.id, cfg)
        return role
    try:
        role = await guild.create_role(
            name=MARRIAGE_ROLE_NAME,
            colour=discord.Colour(HEART_COLOR),
            hoist=False,
            mentionable=False,
            reason="Роль для женатых пар",
        )
    except (discord.Forbidden, discord.HTTPException) as e:
        log.warning("Не смог создать роль брака на сервере %s: %s", getattr(guild, "id", "?"), e)
        return None
    cfg = _cfg(guild.id)
    cfg["marriage_role_id"] = role.id
    _save_cfg(guild.id, cfg)
    return role


async def _give_marriage_role(guild, members):
    role = await _ensure_marriage_role(guild, _cfg(guild.id).get("marriage_role_id"))
    if role is None:
        return
    for m in members:
        if m is None or getattr(m, "bot", False):
            continue
        if role in getattr(m, "roles", []):
            continue
        try:
            await m.add_roles(role, reason="Свадьба")
        except (discord.Forbidden, discord.HTTPException):
            pass


async def _take_marriage_role(guild, members):
    cfg = _cfg(guild.id)
    role_id = cfg.get("marriage_role_id")
    role = guild.get_role(int(role_id)) if role_id else None
    if role is None:
        role = discord.utils.get(getattr(guild, "roles", []), name=MARRIAGE_ROLE_NAME)
    if role is None:
        return
    for m in members:
        if m is None:
            continue
        if role not in getattr(m, "roles", []):
            continue
        try:
            await m.remove_roles(role, reason="Развод")
        except (discord.Forbidden, discord.HTTPException):
            pass


def _gender(member):
    """'male' / 'female' / None (нет роли пола или обе сразу)."""
    ids = {getattr(r, "id", 0) for r in getattr(member, "roles", [])}
    male = MALE_ROLE_ID in ids
    female = FEMALE_ROLE_ID in ids
    if male == female:
        return None
    return "male" if male else "female"


def _marriage_block(a, b) -> str:
    """Почему брак невозможен. Пустая строка — можно."""
    ga, gb = _gender(a), _gender(b)
    if ga is None or gb is None:
        return (
            "Жениться могут только верифицированные — с ролью ♂ или ♀.\n"
            "Нет роли — сначала верификация в проходке."
        )
    if ga == gb:
        return "Брак у нас только ♂ + ♀. Однополые пары не регистрируем 😅"
    return ""


def _alive_partner(guild: discord.Guild, user_id: int):
    """Партнёр брака, если он всё ещё на сервере. Иначе брак считается распавшимся и чистится."""
    m = _partner_of(guild.id, user_id)
    if not m:
        return None, None
    partner_id, since_ts = m
    member = guild.get_member(partner_id)
    if member is None:
        # Партнёр ушёл с сервера — брак больше не считается
        _remove_marriage(guild.id, user_id, partner_id)
        return None, None
    return member, since_ts


class ProposalSession:
    """Одно предложение: сообщение в канале + ЛС цели. Кнопки живут вместе."""

    TIMEOUT = 120

    def __init__(self, proposer: discord.Member, target: discord.Member, guild: discord.Guild):
        self.proposer = proposer
        self.target = target
        self.guild = guild
        self.answered = False
        self.messages: list[discord.Message] = []
        self.views: list["ProposalView"] = []

    def attach(self, message: discord.Message, view: "ProposalView"):
        self.messages.append(message)
        self.views.append(view)

    def stop_all(self):
        for view in self.views:
            view.stop()

    async def edit_all(self, embed: discord.Embed):
        """Правит все сообщения предложения и убирает кнопки (канал + ЛС)."""
        for msg in list(self.messages):
            try:
                await msg.edit(embed=embed, view=None)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    def expire_embed(self) -> discord.Embed:
        name = getattr(self.target, "display_name", "цель")
        return discord.Embed(
            title="⏳ Время вышло",
            description=(
                f"{self.target.mention} не ответил(а) за 2 минуты — предложение сгорело 💔\n"
                f"Попробовать снова: `/marry @{name}`"
            ),
            color=discord.Color.dark_grey(),
        )

    async def expire(self):
        """Таймаут: закрываем везде, чтобы кнопки не выглядели живыми."""
        if self.answered:
            return
        self.answered = True
        self.stop_all()
        await self.edit_all(self.expire_embed())


class ProposalView(discord.ui.View):
    def __init__(self, session: ProposalSession):
        super().__init__(timeout=session.TIMEOUT)
        self.session = session

    @discord.ui.button(label="Согласен 💍", style=discord.ButtonStyle.success)
    async def yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._answer(interaction, True)

    @discord.ui.button(label="Нет 💔", style=discord.ButtonStyle.danger)
    async def no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._answer(interaction, False)

    async def _answer(self, interaction: discord.Interaction, accepted: bool):
        session = self.session
        if interaction.user.id != session.target.id:
            await interaction.response.send_message("Это предложение не тебе 😏", ephemeral=True)
            return
        if session.answered:
            await interaction.response.defer()
            return

        session.answered = True
        session.stop_all()
        # Отвечать можно и из канала, и из ЛС — правим оба сообщения вручную
        await interaction.response.defer()

        guild = session.guild
        proposer, target = session.proposer, session.target

        if accepted:
            # К моменту нажатия кто-то из них мог успеть жениться
            p1, _ = _alive_partner(guild, proposer.id)
            p2, _ = _alive_partner(guild, target.id)
            if p1 or p2:
                await session.edit_all(
                    discord.Embed(
                        title="😔 Не сложилось",
                        description="Кто-то из вас уже успел жениться на другом.",
                        color=discord.Color.red(),
                    )
                )
                _last_propose.pop((guild.id, proposer.id), None)
                return
            # Пол мог поменяться/роли сняться, пока висело предложение
            fresh_a = guild.get_member(proposer.id) or proposer
            fresh_b = guild.get_member(target.id) or target
            gender_block = _marriage_block(fresh_a, fresh_b)
            if gender_block:
                await session.edit_all(
                    discord.Embed(
                        title="😔 Не сложилось",
                        description=gender_block,
                        color=discord.Color.red(),
                    )
                )
                _last_propose.pop((guild.id, proposer.id), None)
                return
            since_ts = time.time()
            _set_marriage(guild.id, proposer.id, target.id, since_ts)
            _last_propose.pop((guild.id, proposer.id), None)
            await _give_marriage_role(guild, [fresh_a, fresh_b])
            days = discord.utils.format_dt(datetime.datetime.fromtimestamp(since_ts), "D")
            embed = discord.Embed(
                title="💍 Свадьба!",
                description=(
                    f"{proposer.mention} и {target.mention} теперь пара!\n\n"
                    f"Свадебная дата: {days}\n"
                    f"У вас есть приватная комната — зайдите в **💗 · Войти** вместе.\n"
                    f"Вам выдана роль **{MARRIAGE_ROLE_NAME}** 💞"
                ),
                color=HEART_COLOR,
                timestamp=datetime.datetime.now(),
            )
            embed.set_footer(text="Горишь ярко ✨ /marriage — узнать, сколько вы вместе")
            await session.edit_all(embed)
        else:
            await session.edit_all(
                discord.Embed(
                    title="💔 Отказ",
                    description=f"{target.mention} отказал {proposer.mention}. Может в другой раз.",
                    color=discord.Color.red(),
                )
            )
            _last_propose.pop((guild.id, proposer.id), None)

    async def on_timeout(self):
        await self.session.expire()


class DivorceView(discord.ui.View):
    def __init__(self, initiator: discord.Member, partner: discord.Member):
        super().__init__(timeout=60)
        self.initiator = initiator
        self.partner = partner

    @discord.ui.button(label="Развестись 💔", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.initiator.id:
            await interaction.response.send_message("Развод только по обоюдному... то есть по личной заявке 😅", ephemeral=True)
            return
        _remove_marriage(interaction.guild.id, interaction.user.id, self.partner.id)
        await _take_marriage_role(interaction.guild, [interaction.user, self.partner])
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="💔 Развод",
                description=f"{interaction.user.mention} и {self.partner.mention} больше не пара.",
                color=discord.Color.red(),
            ),
            view=None,
        )
        self.stop()

    @discord.ui.button(label="Остаться ❤️", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.initiator.id:
            await interaction.response.send_message("Это решает тот, кто подавал на развод.", ephemeral=True)
            return
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="❤️ Мы рады",
                description="Вы передумали. Берегите друг друга.",
                color=HEART_COLOR,
            ),
            view=None,
        )
        self.stop()


# ── Панель управления (persistent view) ───────────────────────────────────────

class RoomPanel(discord.ui.View):
    """Кнопки управления личным каналом. Привязаны к channel_id через custom_id."""

    def __init__(self, voice_channel_id: int):
        super().__init__(timeout=None)
        cid = str(voice_channel_id)
        # Задаём custom_id вручную чтобы они были persistent
        self.children[0].custom_id = f"room_lock_{cid}"
        self.children[1].custom_id = f"room_limit_{cid}"
        self.children[2].custom_id = f"room_rename_{cid}"
        self.children[3].custom_id = f"room_kick_{cid}"
        self.children[4].custom_id = f"room_private_{cid}"
        self.children[5].custom_id = f"room_activity_{cid}"

    @staticmethod
    def _voice_ch(interaction: discord.Interaction, cid: str):
        """Канал берём из кэша бота: панель могут нажать и в ЛС, а там guild = None."""
        try:
            return interaction.client.get_channel(int(cid))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _guild_of(interaction: discord.Interaction, voice_ch):
        """Гильдия канала, а не interaction.guild — в ЛС его нет."""
        return getattr(voice_ch, "guild", None) or interaction.guild

    @classmethod
    def _check_owner(cls, interaction: discord.Interaction, voice_ch) -> bool:
        guild = cls._guild_of(interaction, voice_ch)
        if guild is None:
            return False
        if interaction.user.id == _owner_of(guild.id, voice_ch.id):
            return True
        member = guild.get_member(interaction.user.id)
        return bool(member and member.guild_permissions.administrator)

    # 1. Закрыть / открыть
    @discord.ui.button(label="🔒 Закрыть/открыть", style=discord.ButtonStyle.secondary, custom_id="room_lock_0", row=0)
    async def lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        cid = button.custom_id.split("_", 2)[2]
        vc = self._voice_ch(interaction, cid)
        if not vc or not self._check_owner(interaction, vc):
            await interaction.response.send_message("Нет прав.", ephemeral=True); return
        ow = vc.overwrites_for(vc.guild.default_role)
        currently_locked = ow.connect is False
        ow.connect = None if currently_locked else False
        await vc.set_permissions(vc.guild.default_role, overwrite=ow)
        state = "открыт 🔓" if currently_locked else "закрыт 🔒"
        await interaction.response.send_message(f"Канал {state}.", ephemeral=True)

    # 2. Лимит участников
    @discord.ui.button(label="👥 Лимит", style=discord.ButtonStyle.secondary, custom_id="room_limit_0", row=0)
    async def limit(self, interaction: discord.Interaction, button: discord.ui.Button):
        cid = button.custom_id.split("_", 2)[2]
        vc = self._voice_ch(interaction, cid)
        if not vc or not self._check_owner(interaction, vc):
            await interaction.response.send_message("Нет прав.", ephemeral=True); return
        await interaction.response.send_modal(LimitModal(vc))

    # 3. Переименовать
    @discord.ui.button(label="✏️ Переименовать", style=discord.ButtonStyle.secondary, custom_id="room_rename_0", row=1)
    async def rename(self, interaction: discord.Interaction, button: discord.ui.Button):
        cid = button.custom_id.split("_", 2)[2]
        vc = self._voice_ch(interaction, cid)
        if not vc or not self._check_owner(interaction, vc):
            await interaction.response.send_message("Нет прав.", ephemeral=True); return
        await interaction.response.send_modal(RenameModal(vc))

    # 4. Выгнать участника
    @discord.ui.button(label="🚫 Выгнать", style=discord.ButtonStyle.danger, custom_id="room_kick_0", row=1)
    async def kick_member(self, interaction: discord.Interaction, button: discord.ui.Button):
        cid = button.custom_id.split("_", 2)[2]
        vc = self._voice_ch(interaction, cid)
        if not vc or not self._check_owner(interaction, vc):
            await interaction.response.send_message("Нет прав.", ephemeral=True); return
        members = [m for m in vc.members if m.id != interaction.user.id]
        if not members:
            await interaction.response.send_message("В канале никого нет.", ephemeral=True); return
        await interaction.response.send_message(
            "Кого выгнать?",
            view=KickSelectView(vc, members),
            ephemeral=True,
        )

    # 5. Только для друзей (скрыть от всех / показать)
    @discord.ui.button(label="🤫 Для друзей", style=discord.ButtonStyle.secondary, custom_id="room_private_0", row=2)
    async def private(self, interaction: discord.Interaction, button: discord.ui.Button):
        cid = button.custom_id.split("_", 2)[2]
        vc = self._voice_ch(interaction, cid)
        if not vc or not self._check_owner(interaction, vc):
            await interaction.response.send_message("Нет прав.", ephemeral=True); return
        ow = vc.overwrites_for(vc.guild.default_role)
        currently_hidden = ow.view_channel is False
        ow.view_channel = None if currently_hidden else False
        await vc.set_permissions(vc.guild.default_role, overwrite=ow)
        state = "виден всем 👀" if currently_hidden else "скрыт от остальных 🤫"
        await interaction.response.send_message(f"Канал {state}.", ephemeral=True)

    # 6. Сменить активити
    @discord.ui.button(label="🎮 Активити", style=discord.ButtonStyle.secondary, custom_id="room_activity_0", row=2)
    async def activity(self, interaction: discord.Interaction, button: discord.ui.Button):
        cid = button.custom_id.split("_", 2)[2]
        vc = self._voice_ch(interaction, cid)
        if not vc or not self._check_owner(interaction, vc):
            await interaction.response.send_message("Нет прав.", ephemeral=True); return
        await interaction.response.send_message(
            "Выбери активити:",
            view=ActivitySelectView(vc),
            ephemeral=True,
        )


# ── Модалки ───────────────────────────────────────────────────────────────────

class LimitModal(discord.ui.Modal, title="Лимит участников"):
    value = discord.ui.TextInput(
        label="Лимит (0 = без ограничений, макс 99)",
        placeholder="например: 5",
        min_length=1,
        max_length=2,
    )

    def __init__(self, vc: discord.VoiceChannel):
        super().__init__()
        self.vc = vc

    async def on_submit(self, interaction: discord.Interaction):
        try:
            n = int(self.value.value)
            if not (0 <= n <= 99):
                raise ValueError
        except ValueError:
            await interaction.response.send_message("Введи число от 0 до 99.", ephemeral=True)
            return
        await self.vc.edit(user_limit=n)
        await interaction.response.send_message(
            f"Лимит {'убран' if n == 0 else f'установлен: {n}'}.", ephemeral=True
        )


class RenameModal(discord.ui.Modal, title="Переименовать канал"):
    name = discord.ui.TextInput(
        label="Новое название",
        placeholder="например: уголок Маши",
        min_length=1,
        max_length=32,
    )

    def __init__(self, vc: discord.VoiceChannel):
        super().__init__()
        self.vc = vc

    async def on_submit(self, interaction: discord.Interaction):
        await self.vc.edit(name=self.name.value)
        await interaction.response.send_message(f"Канал переименован: **{self.name.value}**", ephemeral=True)


# ── Выбор участника для кика ──────────────────────────────────────────────────

class KickSelectView(discord.ui.View):
    def __init__(self, vc: discord.VoiceChannel, members: list[discord.Member]):
        super().__init__(timeout=30)
        self.vc = vc
        options = [
            discord.SelectOption(label=m.display_name, value=str(m.id))
            for m in members[:25]
        ]
        select = discord.ui.Select(placeholder="Выбери участника", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        member_id = int(interaction.data["values"][0])
        guild = self.vc.guild
        member = guild.get_member(member_id)
        if member and member.voice and member.voice.channel == self.vc:
            await member.move_to(None, reason=f"Выгнан владельцем {interaction.user}")
            await interaction.response.send_message(f"{member.display_name} выгнан.", ephemeral=True)
        else:
            await interaction.response.send_message("Участник уже не в канале.", ephemeral=True)
        self.stop()


# ── Выбор активити ────────────────────────────────────────────────────────────

ACTIVITIES = [
    ("🎯 Gartic Phone",    "Gartic Phone",    "1007373802981822582"),
    ("🧩 Chess in the Park","Chess",          "832012774040141894"),
    ("🎲 Poker Night",     "Poker Night",     "755827207812677713"),
    ("🎬 Watch Together",  "Watch Together",  "880218394199220334"),
    ("🏖 Sketchheads",     "Sketchheads",     "902271654783242291"),
    ("🌊 Putt Party",      "Putt Party",      "945737671223029840"),
    ("🎮 Blazing 8s",      "Blazing 8s",      "832013003968348200"),
    ("🃏 SpellCast",       "SpellCast",       "852509694341283871"),
    ("🔤 Letter League",   "Letter League",   "879863686565621790"),
    ("🎵 Bobble League",   "Bobble League",   "947957217959759964"),
]


class ActivitySelectView(discord.ui.View):
    def __init__(self, vc: discord.VoiceChannel):
        super().__init__(timeout=30)
        self.vc = vc
        options = [discord.SelectOption(label=label, value=app_id) for label, _, app_id in ACTIVITIES]
        select = discord.ui.Select(placeholder="Выбери активити", options=options)
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction):
        app_id = interaction.data["values"][0]
        label = next((l for l, _, a in ACTIVITIES if a == app_id), "активити")
        try:
            invite = await self.vc.create_invite(
                target_type=discord.InviteTarget.embedded_application,
                target_application_id=int(app_id),
                max_age=3600,
            )
            await interaction.response.send_message(
                f"🎮 **{label}** — [Запустить]({invite.url})", ephemeral=True
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(f"Не удалось создать инвайт: {e}", ephemeral=True)
        self.stop()


# ── Основной ког ──────────────────────────────────────────────────────────────

class Rooms(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @tasks.loop(hours=1)
    async def _anniversaries(self):
        for guild in self.bot.guilds:
            try:
                await self._check_anniversaries(guild)
            except Exception:
                log.exception("Годовщины на сервере %s упали", guild.id)

    @_anniversaries.before_loop
    async def _before_anniversaries(self):
        await self.bot.wait_until_ready()

    async def _check_anniversaries(self, guild: discord.Guild):
        data = _marriages(guild.id)
        seen = set()
        changed = False
        for uid, info in list(data.items()):
            pid = int(info["partner_id"])
            key = (min(int(uid), pid), max(int(uid), pid))
            if key in seen:
                continue
            seen.add(key)
            days = _days_together(float(info.get("since_ts", 0)))
            reached = _reached_milestones(days, info.get("anniv"))
            if not reached:
                continue
            a = guild.get_member(key[0])
            b = guild.get_member(key[1])
            if a is None or b is None:
                continue
            mark = sorted({int(d) for d in (info.get("anniv") or [])} | set(reached))
            for u in key:
                entry = data.get(str(u))
                if entry is not None:
                    entry["anniv"] = mark
            changed = True
            await self._announce_anniversary(guild, a, b, max(reached), days)
        if changed:
            _save_marriages(guild.id, data)

    async def _announce_anniversary(self, guild, a, b, milestone: int, days: int):
        embed = discord.Embed(
            title=f"🥂 {_milestone_text(milestone).capitalize()}!",
            description=(
                f"{a.mention} 💞 {b.mention}\n\n"
                f"Вы вместе уже **{days}** дн. Берегите друг друга 💜"
            ),
            color=HEART_COLOR,
            timestamp=datetime.datetime.now(),
        )
        # 1) личный чат пары, 2) канал поздравлений, 3) ЛС
        _vc_id, room = _couple_room_for(guild.id, a.id, b.id)
        text_id = room.get("text_channel_id") if room else None
        text_ch = guild.get_channel(int(text_id)) if text_id else None
        if text_ch is not None:
            try:
                await text_ch.send(embed=embed)
                return
            except discord.HTTPException:
                pass
        ann_id = int(_cfg(guild.id).get("couple_announce_id") or 0)
        ann_ch = guild.get_channel(ann_id) if ann_id else None
        if ann_ch is not None:
            try:
                await ann_ch.send(embed=embed)
                return
            except discord.HTTPException:
                pass
        for m in (a, b):
            try:
                await m.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass

    @commands.Cog.listener()
    async def on_ready(self):
        # Годовщины: запускаем раз в час, только после реального старта
        if not self._anniversaries.is_running():
            self._anniversaries.start()
        # Восстановить persistent views для всех живых комнат + вычистить умершие
        data = load(ROOMS_FILE, {})
        for guild_id, rooms in data.items():
            guild = self.bot.get_guild(int(guild_id))
            changed = False
            for ch_id in list(rooms):
                info = rooms[ch_id]
                try:
                    self.bot.add_view(RoomPanel(int(ch_id)))
                except Exception:
                    pass
                vc = guild.get_channel(int(ch_id)) if guild else None
                if vc is not None:
                    continue
                # Голосовой комнаты уже нет — убираем запись и парный чат
                text_id = info.get("text_channel_id")
                if guild and text_id:
                    text = guild.get_channel(int(text_id))
                    if text is not None:
                        try:
                            await text.delete(reason="Парная комната пропала")
                        except discord.HTTPException:
                            pass
                rooms.pop(ch_id, None)
                changed = True
            if changed:
                _save_rooms(int(guild_id), rooms)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Партнёр ушёл с сервера — брак распадается, роль снимаем."""
        pair = _partner_of(member.guild.id, member.id)
        if not pair:
            return
        pid, _since = pair
        _remove_marriage(member.guild.id, member.id, pid)
        partner = member.guild.get_member(pid)
        if partner is not None:
            await _take_marriage_role(member.guild, [partner])

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ):
        guild = member.guild
        cfg = _cfg(guild.id)
        trigger_id = int(cfg.get("trigger_channel_id") or DEFAULT_TRIGGER_CH)
        category_id = int(cfg.get("category_id") or DEFAULT_CATEGORY)
        panel_ch_id = int(cfg.get("panel_channel_id") or DEFAULT_PANEL_CH)
        couple_trigger_id = int(cfg.get("couple_trigger_id") or 0)

        # ── Парная комната (💗 · Войти) ───────────────────────────
        if couple_trigger_id and after.channel and after.channel.id == couple_trigger_id:
            await self._create_couple_room(member, guild, cfg)

        # ── Создание комнаты ──────────────────────────────────────
        if after.channel and after.channel.id == trigger_id:
            category = guild.get_channel(category_id)
            rooms = _rooms(guild.id)

            # Имя канала
            ch_name = f"🛋 {member.display_name}"

            try:
                vc = await guild.create_voice_channel(
                    ch_name,
                    category=category,
                    reason=f"Личный уголок {member}",
                )
                # Владелец получает manage_channels чтобы видеть панель управления
                await vc.set_permissions(
                    member,
                    view_channel=True,
                    connect=True,
                    speak=True,
                    manage_channels=True,
                )
            except discord.HTTPException:
                return

            # Переместить участника
            try:
                await member.move_to(vc)
            except discord.HTTPException:
                await vc.delete()
                return

            # Сохранить
            rooms[str(vc.id)] = {
                "owner_id": member.id,
                "owner_name": str(member),
                "created_at": datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
            }
            _save_rooms(guild.id, rooms)

            # Панель управления — шлём в ЛС владельцу
            embed = discord.Embed(
                title="🏠 Твой уголок",
                description=(
                    f"Твой канал: {vc.mention}\n"
                    "Используй кнопки ниже для управления."
                ),
                color=discord.Color.from_str("#a9c9ff"),
                timestamp=datetime.datetime.now(),
            )
            embed.set_footer(text=f"Владелец: {member.display_name}")
            view = RoomPanel(vc.id)
            try:
                await member.send(embed=embed, view=view)
            except discord.Forbidden:
                # ЛС закрыты — постим в канал панели как запасной вариант
                panel_ch = guild.get_channel(panel_ch_id)
                if panel_ch:
                    try:
                        msg = await panel_ch.send(
                            content=member.mention,
                            embed=embed,
                            view=view,
                        )
                        rooms[str(vc.id)]["panel_msg_id"] = msg.id
                        rooms[str(vc.id)]["panel_ch_id"] = panel_ch.id
                        _save_rooms(guild.id, rooms)
                    except discord.HTTPException:
                        pass

        # ── Удаление пустой комнаты ───────────────────────────────
        if before.channel and before.channel.id != trigger_id:
            rooms = _rooms(guild.id)
            info = rooms.get(str(before.channel.id))
            if info and len(before.channel.members) == 0:
                # Удалить панель
                panel_msg_id = info.get("panel_msg_id")
                panel_ch_id_saved = info.get("panel_ch_id")
                if panel_msg_id and panel_ch_id_saved:
                    panel_ch = guild.get_channel(int(panel_ch_id_saved))
                    if panel_ch:
                        try:
                            msg = await panel_ch.fetch_message(int(panel_msg_id))
                            await msg.delete()
                        except (discord.HTTPException, discord.NotFound):
                            pass
                # Парный текстовый чат живёт вместе с голосовым
                text_id = info.get("text_channel_id")
                if text_id:
                    text_ch = guild.get_channel(int(text_id))
                    if text_ch is not None:
                        try:
                            await text_ch.delete(reason="Гнёздышко опустело")
                        except discord.HTTPException:
                            pass
                # Удалить канал
                try:
                    await before.channel.delete(reason="Уголок опустел")
                except discord.HTTPException:
                    pass
                del rooms[str(before.channel.id)]
                _save_rooms(guild.id, rooms)

    async def _create_couple_room(self, member: discord.Member, guild: discord.Guild, cfg: dict):
        partner, _since = _alive_partner(guild, member.id)
        if partner is None:
            # Не женат — гнёздышко не создаём
            await self._couple_room_refused(member, guild, cfg)
            return

        # Уже есть гнездо (своё или партнёра) — просто подселяем, без дубля
        rooms = _rooms(guild.id)
        for owner in (member.id, partner.id if partner else None):
            if owner is None:
                continue
            for ch_id, info in rooms.items():
                if info.get("kind") != "couple" or str(info.get("owner_id")) != str(owner):
                    continue
                vc = guild.get_channel(int(ch_id))
                if vc is None:
                    continue
                if partner is not None:
                    # Свадьба случилась после создания комнаты — доподключаем вторую половинку
                    try:
                        await vc.set_permissions(
                            partner, view_channel=True, connect=True, speak=True
                        )
                    except discord.HTTPException:
                        pass
                    text_id = info.get("text_channel_id")
                    text_ch = guild.get_channel(int(text_id)) if text_id else None
                    if text_ch is not None:
                        try:
                            await text_ch.set_permissions(
                                partner,
                                view_channel=True,
                                send_messages=True,
                                read_message_history=True,
                                attach_files=True,
                                embed_links=True,
                            )
                        except discord.HTTPException:
                            pass
                    if str(info.get("partner_id")) != str(partner.id):
                        info["partner_id"] = partner.id
                        _save_rooms(guild.id, rooms)
                try:
                    await member.move_to(vc)
                except discord.HTTPException:
                    pass
                return

        category_id = int(cfg.get("couple_category_id") or 0)
        category = guild.get_channel(category_id)
        if category is None:
            category = None  # без категории — упадёт в корень, но не умрём

        limit = max(2, int(cfg.get("couple_limit") or DEFAULT_COUPLE_LIMIT))
        make_text = bool(cfg.get("couple_text_chat", True))

        if partner:
            base = f"{member.display_name} & {partner.display_name}"
        else:
            base = member.display_name
        ch_name = f"💗 {base}"[:100]

        try:
            vc = await guild.create_voice_channel(
                ch_name,
                category=category,
                user_limit=limit,
                reason=f"Парная комната {member}",
            )
            # Приватная: только владелец (+ партнёр) видят и заходят
            await vc.set_permissions(
                guild.default_role, view_channel=False, connect=False
            )
            await vc.set_permissions(
                member, view_channel=True, connect=True, manage_channels=True, speak=True
            )
            if partner:
                await vc.set_permissions(partner, view_channel=True, connect=True, speak=True)
            # Стаффу тут делать нечего: права категории иначе пускают их внутрь
            await _hide_from_staff(vc, guild, voice=True)
        except discord.HTTPException:
            return

        # Общий текстовый чат на двоих — рядом с голосовым
        text_ch = None
        if make_text:
            owner_perms = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
                manage_messages=True,
            )
            partner_perms = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                attach_files=True,
                embed_links=True,
            )
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                member: owner_perms,
            }
            if partner:
                overwrites[partner] = partner_perms
            # Сразу закрываем и от стаффа, без окна видимости
            for hidden_role in _roles_to_hide(guild):
                overwrites[hidden_role] = discord.PermissionOverwrite(view_channel=False)
            try:
                text_ch = await guild.create_text_channel(
                    f"💬 {base}"[:100],
                    category=category,
                    overwrites=overwrites,
                    topic=f"Личный чат пары — {base}"[:1024],
                    reason=f"Парный чат {member}",
                )
            except discord.HTTPException:
                text_ch = None

        try:
            await member.move_to(vc)
        except discord.HTTPException:
            await vc.delete()
            if text_ch is not None:
                try:
                    await text_ch.delete()
                except discord.HTTPException:
                    pass
            return

        rooms = _rooms(guild.id)
        rooms[str(vc.id)] = {
            "owner_id": member.id,
            "owner_name": str(member),
            "kind": "couple",
            "partner_id": (partner.id if partner else None),
            "text_channel_id": (text_ch.id if text_ch else None),
            "created_at": datetime.datetime.now().strftime("%d.%m.%Y %H:%M"),
        }
        _save_rooms(guild.id, rooms)

        if text_ch is not None:
            try:
                msg = await text_ch.send(
                    embed=discord.Embed(
                        title="💌 Ваш личный чат",
                        description=(
                            f"Здесь видите только вы двое. Обсуждайте что угодно — никто не подслушает.\n"
                            f"`/marriage` — сколько вы уже вместе и когда ближайший юбилей."
                        ),
                        color=HEART_COLOR,
                    )
                )
                await msg.pin(reason="Приветствие парного чата")
            except discord.HTTPException:
                pass

        if partner:
            together = _partner_of(guild.id, member.id)
            days = _days_together(together[1]) if together else 0
            desc = (
                f"Ваше гнёздышко: {vc.mention}\n"
                + (f"Личный чат: {text_ch.mention}\n" if text_ch else "")
                + f"Комната скрыта от всех, кроме вас двоих.\n"
                f"Вы вместе {days} дн. 💕"
            )
        else:
            desc = (
                f"Ваша комната: {vc.mention}\n"
                + (f"Личный чат: {text_ch.mention}\n" if text_ch else "")
                + f"Она скрыта от остальных. Позовите вторую половинку через настройки канала,\n"
                f"а чтобы она появилась сама — женитесь: `/marry @ник` 💍"
            )
        embed = discord.Embed(
            title="💜 Гнёздышко для двоих",
            description=desc,
            color=HEART_COLOR,
            timestamp=datetime.datetime.now(),
        )
        view = RoomPanel(vc.id)
        try:
            await member.send(embed=embed, view=view)
        except discord.Forbidden:
            # ЛС закрыты — постим панель в канал пар (или в общую панель)
            fallback_id = int(cfg.get("couple_panel_id") or cfg.get("panel_channel_id") or 0)
            fallback = guild.get_channel(fallback_id) if fallback_id else None
            if fallback is not None:
                try:
                    msg = await fallback.send(content=member.mention, embed=embed, view=view)
                    rooms[str(vc.id)]["panel_msg_id"] = msg.id
                    rooms[str(vc.id)]["panel_ch_id"] = fallback.id
                    _save_rooms(guild.id, rooms)
                except discord.HTTPException:
                    pass

    async def _couple_room_refused(self, member, guild, cfg):
        """Вошёл в «💗 · Войти» без пары — комнату не делаем, объясняем."""
        embed = discord.Embed(
            title="💗 Только для пар",
            description=(
                "Комната для двоих создаётся только для женатых.\n"
                "Сначала предложение: `/marry @ник` — потом заходите сюда вдвоём 💍"
            ),
            color=HEART_COLOR,
        )
        try:
            await member.send(embed=embed)
            return
        except discord.Forbidden:
            pass
        except discord.HTTPException:
            return
        fallback_id = int(cfg.get("couple_panel_id") or cfg.get("panel_channel_id") or 0)
        fallback = guild.get_channel(fallback_id) if fallback_id else None
        if fallback is not None:
            try:
                await fallback.send(content=member.mention, embed=embed, delete_after=15)
            except discord.HTTPException:
                pass

    # ── /my_room ──────────────────────────────────────────────────────────────

    @app_commands.command(name="my_room", description="Получить панель управления своим уголком")
    async def my_room(self, interaction: discord.Interaction):
        rooms = _rooms(interaction.guild.id)
        # Комната, где этот участник владелец или вторая половинка
        me = str(interaction.user.id)
        my = next(
            ((ch_id, info) for ch_id, info in rooms.items()
             if me in (str(info.get("owner_id")), str(info.get("partner_id")))),
            None,
        )
        if not my:
            await interaction.response.send_message("У тебя нет активного уголка.", ephemeral=True)
            return
        ch_id, info = my
        vc = interaction.guild.get_channel(int(ch_id))
        if not vc:
            await interaction.response.send_message("Уголок не найден — возможно уже удалён.", ephemeral=True)
            return
        couple = info.get("kind") == "couple"
        if couple:
            title = "💜 Ваше гнёздышко"
            desc = f"Ваш канал: {vc.mention}\n"
            text_id = info.get("text_channel_id")
            text_ch = interaction.guild.get_channel(int(text_id)) if text_id else None
            if text_ch is not None:
                desc += f"Личный чат: {text_ch.mention}\n"
            desc += "Кнопки ниже — управление комнатой (доступны обоим)."
            color = HEART_COLOR
        else:
            title = "🏠 Твой уголок"
            desc = f"Твой канал: {vc.mention}\nИспользуй кнопки ниже для управления."
            color = discord.Color.from_str("#a9c9ff")
        embed = discord.Embed(title=title, description=desc, color=color)
        embed.set_footer(text=f"Владелец: {info.get('owner_name') or interaction.user.display_name}")
        await interaction.response.send_message(embed=embed, view=RoomPanel(vc.id), ephemeral=True)

    # ── /setup_rooms ──────────────────────────────────────────────────────────

    @app_commands.command(name="setup_rooms", description="Настроить личные голосовые каналы")
    @app_commands.describe(
        trigger="Канал-триггер (войти = создать уголок)",
        category="Категория, где создаются уголки",
        panel="Текстовый канал с кнопками управления",
        couple_trigger="Канал-триггер для парных комнат (💗 · Войти)",
        couple_category="Категория, где создаются парные комнаты (💜 Мурмир)",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_rooms(
        self,
        interaction: discord.Interaction,
        trigger: discord.VoiceChannel,
        category: discord.CategoryChannel,
        panel: discord.TextChannel,
        couple_trigger: discord.VoiceChannel = None,
        couple_category: discord.CategoryChannel = None,
    ):
        cfg = _cfg(interaction.guild.id)
        cfg["trigger_channel_id"] = trigger.id
        cfg["category_id"] = category.id
        cfg["panel_channel_id"] = panel.id
        if couple_trigger is not None:
            cfg["couple_trigger_id"] = couple_trigger.id
        if couple_category is not None:
            cfg["couple_category_id"] = couple_category.id
        elif couple_trigger is not None and couple_trigger.category_id:
            cfg["couple_category_id"] = couple_trigger.category_id
        _save_cfg(interaction.guild.id, cfg)

        embed = discord.Embed(
            title="✅ Личные каналы настроены",
            color=discord.Color.green(),
        )
        embed.add_field(name="Триггер", value=trigger.mention, inline=True)
        embed.add_field(name="Категория", value=category.mention, inline=True)
        embed.add_field(name="Панель", value=panel.mention, inline=True)
        ct = int(cfg.get("couple_trigger_id") or 0)
        cc = int(cfg.get("couple_category_id") or 0)
        ct_ch = interaction.guild.get_channel(ct) if ct else None
        cc_ch = interaction.guild.get_channel(cc) if cc else None
        embed.add_field(
            name="Парные 💗",
            value=(f"{ct_ch.mention} → {cc_ch.mention}" if ct_ch and cc_ch else "не настроено"),
            inline=False,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /setup_couples ────────────────────────────────────────────────────────

    @app_commands.command(
        name="setup_couples",
        description="Настроить парные комнаты и плюшки для пар 💜",
    )
    @app_commands.describe(
        trigger="Канал-триггер: вошёл — получил гнёздышко",
        category="Категория для парных комнат (по умолчанию — категория триггера)",
        limit="Лимит участников в парной комнате (по умолчанию 2)",
        text_chat="Создавать паре общий текстовый чат",
        marriage_role="Роль для женатых (по умолчанию «💍 В браке»)",
        panel="Куда кидать панель, если у пары закрыты ЛС",
        announce="Канал для поздравлений с годовщинами",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_couples(
        self,
        interaction: discord.Interaction,
        trigger: discord.VoiceChannel = None,
        category: discord.CategoryChannel = None,
        limit: app_commands.Range[int, 2, 25] = None,
        text_chat: bool = None,
        marriage_role: discord.Role = None,
        panel: discord.TextChannel = None,
        announce: discord.TextChannel = None,
    ):
        cfg = _cfg(interaction.guild.id)
        if trigger is not None:
            cfg["couple_trigger_id"] = trigger.id
        if category is not None:
            cfg["couple_category_id"] = category.id
        elif trigger is not None and trigger.category_id:
            cfg["couple_category_id"] = trigger.category_id
        if limit is not None:
            cfg["couple_limit"] = int(limit)
        if text_chat is not None:
            cfg["couple_text_chat"] = bool(text_chat)
        if marriage_role is not None:
            cfg["marriage_role_id"] = marriage_role.id
        if panel is not None:
            cfg["couple_panel_id"] = panel.id
        if announce is not None:
            cfg["couple_announce_id"] = announce.id
        _save_cfg(interaction.guild.id, cfg)

        # Старые комнаты тоже прячем: права категории иначе пускают стафф внутрь
        fixed = await _hide_existing_couple_rooms(interaction.guild)

        g = interaction.guild
        ct = int(cfg.get("couple_trigger_id") or 0)
        cc = int(cfg.get("couple_category_id") or 0)
        ct_ch = g.get_channel(ct) if ct else None
        cc_ch = g.get_channel(cc) if cc else None
        role_id = int(cfg.get("marriage_role_id") or 0)
        role = g.get_role(role_id) if role_id else None
        an = int(cfg.get("couple_announce_id") or 0)
        ann_ch = g.get_channel(an) if an else None
        lim = int(cfg.get("couple_limit") or DEFAULT_COUPLE_LIMIT)
        text_on = bool(cfg.get("couple_text_chat", True))

        embed = discord.Embed(
            title="💜 Парные комнаты",
            color=HEART_COLOR,
            description=(
                "Пара заходит в канал-триггер — получает приватную комнату и чат на двоих."
                if ct_ch
                else "⚠️ Триггер не задан — парные комнаты выключены. Укажи `trigger`."
            ),
        )
        embed.add_field(name="Триггер", value=ct_ch.mention if ct_ch else "—", inline=True)
        embed.add_field(name="Категория", value=cc_ch.mention if cc_ch else "—", inline=True)
        embed.add_field(name="Лимит", value=str(lim), inline=True)
        embed.add_field(name="Текст-чат пары", value="вкл" if text_on else "выкл", inline=True)
        embed.add_field(
            name="Роль брака",
            value=role.mention if role else f"{MARRIAGE_ROLE_NAME} (создам при первой свадьбе)",
            inline=True,
        )
        pn = int(cfg.get("couple_panel_id") or 0)
        pn_ch = g.get_channel(pn) if pn else None
        embed.add_field(name="Панель (если ЛС закрыты)", value=pn_ch.mention if pn_ch else "—", inline=True)
        embed.add_field(name="Канал годовщин", value=ann_ch.mention if ann_ch else "ЛС/чат пары", inline=True)
        hidden_roles = _roles_to_hide(g)
        privacy = (
            f"комнаты закрыты от всех ролей ({len(hidden_roles)}), видят только двое"
            if hidden_roles
            else "ролей не нашёл — комнаты закрыты только от @everyone"
        )
        privacy += "\n_Админы видят всё — это ограничение Discord._"
        embed.add_field(name="Приватность", value=privacy, inline=False)
        if fixed:
            embed.add_field(name="Перепрятано старых комнат", value=str(fixed), inline=True)
        embed.set_footer(text="Плюшки: роль брака, общий чат, годовщины — /marriage")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── Свадьбы ───────────────────────────────────────────────────────────────

    @app_commands.command(name="marry", description="Предложить руку и сердце 💍")
    @app_commands.describe(user="Кому предлагаешь")
    @app_commands.checks.cooldown(1, 60.0, key=lambda i: (i.guild_id, i.user.id))
    async def marry(self, interaction: discord.Interaction, user: discord.Member):
        guild = interaction.guild
        if user.id == interaction.user.id:
            await interaction.response.send_message("Себя женить нельзя, самодостаточность ещё никому не помогла 😅", ephemeral=True)
            return
        if user.bot:
            await interaction.response.send_message("Боты пока не женятся. Законы о роботах не приняты.", ephemeral=True)
            return

        block = _marriage_block(interaction.user, user)
        if block:
            await interaction.response.send_message(block, ephemeral=True)
            return

        my_partner, _ = _alive_partner(guild, interaction.user.id)
        if my_partner:
            await interaction.response.send_message(
                f"Ты уже в браке с {my_partner.mention}. Сначала развод: `/divorce`.", ephemeral=True
            )
            return
        their_partner, _ = _alive_partner(guild, user.id)
        if their_partner:
            await interaction.response.send_message(
                f"{user.mention} уже занят(а) — в браке с {their_partner.mention} 💍", ephemeral=True
            )
            return

        last = _last_propose.get((guild.id, interaction.user.id), 0)
        if time.time() - last < 120:
            await interaction.response.send_message("Недавно тебе уже отказали. Дай людям подумать 😅", ephemeral=True)
            return
        _last_propose[(guild.id, interaction.user.id)] = time.time()

        embed = discord.Embed(
            title="💍 Вопрос на миллион",
            description=(
                f"{interaction.user.mention} делает предложение {user.mention}!\n\n"
                f"{user.mention}, согласен(а)? У тебя 2 минуты."
            ),
            color=HEART_COLOR,
            timestamp=datetime.datetime.now(),
        )

        session = ProposalSession(interaction.user, user, guild)
        channel_view = ProposalView(session)
        await interaction.response.send_message(embed=embed, view=channel_view)
        try:
            channel_msg = await interaction.original_response()
        except discord.HTTPException:
            channel_msg = None
        if channel_msg is not None:
            session.attach(channel_msg, channel_view)

        # Дублируем в ЛС: в шумном канале предложение легко пропустить
        dm_embed = discord.Embed(
            title="💍 Тебе сделали предложение",
            description=(
                f"**{interaction.user.display_name}** делает тебе предложение "
                f"на сервере **{guild.name}**.\n\n"
                "У тебя 2 минуты. Ответить можно прямо здесь — "
                "сообщение в канале обновится само."
            ),
            color=HEART_COLOR,
            timestamp=datetime.datetime.now(),
        )
        dm_view = ProposalView(session)
        try:
            dm_msg = await user.send(embed=dm_embed, view=dm_view)
        except discord.Forbidden:
            # ЛС закрыты — кладём копию в канал пар, если он настроен
            cfg = _cfg(guild.id)
            fb_id = int(cfg.get("couple_panel_id") or cfg.get("panel_channel_id") or 0)
            fallback = guild.get_channel(fb_id) if fb_id else None
            posted = False
            if fallback is not None:
                try:
                    fb_msg = await fallback.send(
                        content=user.mention, embed=dm_embed, view=dm_view
                    )
                except (discord.Forbidden, discord.HTTPException):
                    pass
                else:
                    session.attach(fb_msg, dm_view)
                    posted = True
            if not posted:
                await interaction.followup.send(
                    f"⚠️ У {user.mention} закрыты ЛС — предложение видно только в канале.",
                    ephemeral=True,
                )
        except discord.HTTPException:
            pass
        else:
            session.attach(dm_msg, dm_view)

    @app_commands.command(name="divorce", description="Развестись 💔")
    async def divorce(self, interaction: discord.Interaction):
        guild = interaction.guild
        partner, since = _alive_partner(guild, interaction.user.id)
        if not partner:
            await interaction.response.send_message("Ты не в браке. Расстраиваться не о чем.", ephemeral=True)
            return
        days = _days_together(since)
        embed = discord.Embed(
            title="💔 Точно развод?",
            description=(
                f"Вы с {partner.mention} вместе {days} дн.\n"
                f"Подумай ещё раз — кнопки внизу."
            ),
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, view=DivorceView(interaction.user, partner))

    @app_commands.command(name="marriage", description="Твой брак: кто, сколько дней 💕")
    @app_commands.describe(user="Чей брак посмотреть (по умолчанию твой)")
    async def marriage(self, interaction: discord.Interaction, user: discord.Member = None):
        member = user or interaction.user
        partner, since = _alive_partner(interaction.guild, member.id)
        if not partner:
            who = "Ты" if member.id == interaction.user.id else f"{member.display_name}"
            await interaction.response.send_message(
                f"{who} свободен(на) 💫 Сердце ищет пару — `/marry @ник`.",
                ephemeral=True,
            )
            return
        days = _days_together(since)
        date = discord.utils.format_dt(datetime.datetime.fromtimestamp(since), "D")
        nxt = _next_milestone(days)
        if nxt is not None:
            when = f"До юбилея «{_milestone_text(nxt)}»: **{nxt - days}** дн."
        else:
            when = "Все юбилеи пройдены — вы легенда сервера 🏆"
        embed = discord.Embed(
            title="💕 Союз двух сердец",
            description=(
                f"{member.mention} 💞 {partner.mention}\n\n"
                f"Свадьба: {date}\n"
                f"Вместе уже **{days}** дн.\n\n"
                f"{when}"
            ),
            color=HEART_COLOR,
            timestamp=datetime.datetime.now(),
        )
        embed.set_footer(text="Уголок для двоих: 💗 · Войти · Годовщины отмечаем сами")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="couples", description="Самые крепкие пары сервера 💞")
    async def couples(self, interaction: discord.Interaction):
        data = _marriages(interaction.guild.id)
        seen = set()
        rows = []
        for uid, info in data.items():
            pid = int(info["partner_id"])
            key = min(int(uid), pid), max(int(uid), pid)
            if key in seen:
                continue
            seen.add(key)
            a = interaction.guild.get_member(int(uid))
            b = interaction.guild.get_member(pid)
            if not a or not b:
                continue
            rows.append((float(info.get("since_ts", 0)), a, b))
        if not rows:
            await interaction.response.send_message(
                "Пока никого. Стань первой парой сервера — `/marry @ник` 💍", ephemeral=True
            )
            return
        rows.sort(key=lambda r: r[0])
        medals = ["🥇", "🥈", "🥉"] + ["💜"] * 7
        lines = []
        for i, (since, a, b) in enumerate(rows[:10]):
            days = _days_together(since)
            lines.append(f"{medals[i]} {a.mention} 💞 {b.mention} — **{days}** дн.")
        embed = discord.Embed(
            title="💞 Самые крепкие пары",
            description="\n".join(lines),
            color=HEART_COLOR,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="rooms", description="Список активных уголков")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def rooms_list(self, interaction: discord.Interaction):
        rooms = _rooms(interaction.guild.id)
        if not rooms:
            await interaction.response.send_message("Нет активных уголков.", ephemeral=True)
            return
        lines = []
        for ch_id, info in rooms.items():
            ch = interaction.guild.get_channel(int(ch_id))
            ch_ref = ch.mention if ch else f"#{ch_id}"
            lines.append(f"{ch_ref} — <@{info['owner_id']}> ({info.get('created_at', '—')})")
        embed = discord.Embed(
            title=f"🏠 Активные уголки: {len(rooms)}",
            description="\n".join(lines),
            color=discord.Color.from_str("#a9c9ff"),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Rooms(bot))
