# -*- coding: utf-8 -*-
"""Личные голосовые каналы.

Человек заходит в «➕ Создать уголок» → автоматически создаётся личный канал
в той же категории с кнопками управления в текстовом канале-панели.
"""

import asyncio
import datetime

import discord
from discord import app_commands
from discord.ext import commands

from storage import load, save

ROOMS_FILE = "rooms.json"
SETTINGS_FILE = "settings.json"

# ID канала-триггера и категории на сервере Roet (можно переопределить через /setup_rooms)
DEFAULT_TRIGGER_CH = 1573423260513607771   # ➕ · Создать уголок
DEFAULT_CATEGORY   = 1573423253500985344   # 🏠˚ Свой уголок
DEFAULT_PANEL_CH   = 1573423257724395575   # 🔑--мой-уголок


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
        return interaction.guild.get_channel(int(cid))

    @staticmethod
    def _check_owner(interaction: discord.Interaction, voice_ch: discord.VoiceChannel) -> bool:
        owner_id = _owner_of(interaction.guild.id, voice_ch.id)
        return interaction.user.id == owner_id or interaction.user.guild_permissions.administrator

    # 1. Закрыть / открыть
    @discord.ui.button(label="🔒 Закрыть/открыть", style=discord.ButtonStyle.secondary, custom_id="room_lock_0", row=0)
    async def lock(self, interaction: discord.Interaction, button: discord.ui.Button):
        cid = button.custom_id.split("_", 2)[2]
        vc = self._voice_ch(interaction, cid)
        if not vc or not self._check_owner(interaction, vc):
            await interaction.response.send_message("Нет прав.", ephemeral=True); return
        ow = vc.overwrites_for(interaction.guild.default_role)
        currently_locked = ow.connect is False
        ow.connect = None if currently_locked else False
        await vc.set_permissions(interaction.guild.default_role, overwrite=ow)
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
        ow = vc.overwrites_for(interaction.guild.default_role)
        currently_hidden = ow.view_channel is False
        ow.view_channel = None if currently_hidden else False
        await vc.set_permissions(interaction.guild.default_role, overwrite=ow)
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
        member = interaction.guild.get_member(member_id)
        if member and member.voice and member.voice.channel == self.vc:
            await member.move_to(None, reason=f"Выгнан владельцем {interaction.user}")
            await interaction.response.send_message(f"{member.display_name} выгнан.", ephemeral=True)
        else:
            await interaction.response.send_message("Участник уже не в канале.", ephemeral=True)
        self.stop()


# ── Выбор активити ────────────────────────────────────────────────────────────

ACTIVITIES = [
    ("🎯 Gartic Phone",   "Gartic Phone",   "879863881349087252"),
    ("🧩 Chess",          "Chess",           "832012774040141894"),
    ("🎲 Poker Night",    "Poker Night",     "755827207812677713"),
    ("🎬 Watch Together", "Watch Together",  "880218394199220334"),
    ("🎸 Bobble League",  "Bobble League",   "947957217959759964"),
    ("🏖 Sketchheads",    "Sketchheads",     "902271654783242291"),
    ("🌊 Putt Party",     "Putt Party",      "945737671223029840"),
    ("🎵 Spotify",        "Spotify",         "914090279785586749"),
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

    @commands.Cog.listener()
    async def on_ready(self):
        # Восстановить persistent views для всех живых комнат
        data = load(ROOMS_FILE, {})
        for guild_id, rooms in data.items():
            for ch_id in rooms:
                try:
                    self.bot.add_view(RoomPanel(int(ch_id)))
                except Exception:
                    pass

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

            # Панель управления
            panel_ch = guild.get_channel(panel_ch_id)
            if panel_ch:
                embed = discord.Embed(
                    title="🏠 Твой уголок",
                    description=(
                        f"{member.mention}, твой канал: {vc.mention}\n"
                        "Используй кнопки ниже для управления."
                    ),
                    color=discord.Color.from_str("#a9c9ff"),
                    timestamp=datetime.datetime.now(),
                )
                embed.set_footer(text=f"Владелец: {member.display_name}")
                view = RoomPanel(vc.id)
                try:
                    msg = await panel_ch.send(embed=embed, view=view)
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
                # Удалить канал
                try:
                    await before.channel.delete(reason="Уголок опустел")
                except discord.HTTPException:
                    pass
                del rooms[str(before.channel.id)]
                _save_rooms(guild.id, rooms)

    # ── /setup_rooms ──────────────────────────────────────────────────────────

    @app_commands.command(name="setup_rooms", description="Настроить личные голосовые каналы")
    @app_commands.describe(
        trigger="Канал-триггер (войти = создать уголок)",
        category="Категория, где создаются уголки",
        panel="Текстовый канал с кнопками управления",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def setup_rooms(
        self,
        interaction: discord.Interaction,
        trigger: discord.VoiceChannel,
        category: discord.CategoryChannel,
        panel: discord.TextChannel,
    ):
        cfg = _cfg(interaction.guild.id)
        cfg["trigger_channel_id"] = trigger.id
        cfg["category_id"] = category.id
        cfg["panel_channel_id"] = panel.id
        _save_cfg(interaction.guild.id, cfg)

        embed = discord.Embed(
            title="✅ Личные каналы настроены",
            color=discord.Color.green(),
        )
        embed.add_field(name="Триггер", value=trigger.mention, inline=True)
        embed.add_field(name="Категория", value=category.mention, inline=True)
        embed.add_field(name="Панель", value=panel.mention, inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

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
