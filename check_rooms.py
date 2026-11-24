# -*- coding: utf-8 -*-
"""Проверка свадеб и парных комнат bot-rooms без Discord (временная папка data)."""

import os
import sys
import tempfile
import time
from types import SimpleNamespace

import asyncio

TMP = tempfile.mkdtemp(prefix="rooms_check_")
sys.path.insert(0, r"C:\Users\tomoe\Downloads\bot-rooms")

import storage
storage.DATA_DIR = os.path.join(TMP, "data")

from cogs import rooms as rc

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        fails.append(name)


class FakeRole:
    def __init__(self, rid):
        self.id = rid


class FakeMember:
    def __init__(self, uid, roles=()):
        self.id = uid
        self.roles = [FakeRole(r) for r in roles]


class FakeGuild:
    def __init__(self, ids):
        self.id = 555
        self._ids = ids

    def get_member(self, uid):
        return FakeMember(uid) if uid in self._ids else None


g = FakeGuild({1, 2, 3})

# Брак: взаимность
rc._set_marriage(555, 1, 2, time.time() - 86400 * 3)
p1, since1 = rc._partner_of(555, 1)
p2, since2 = rc._partner_of(555, 2)
check("marriage mutual", p1 == 2 and p2 == 1 and since1 == since2)
check("days together", 2 <= rc._days_together(since1) <= 3, str(rc._days_together(since1)))
check("single is none", rc._partner_of(555, 3) is None)

# Партнёр на сервере
member, since = rc._alive_partner(g, 1)
check("partner alive", member is not None and member.id == 2)

# Партнёр ушёл — брак чистится
rc._set_marriage(555, 2, 99, time.time())  # 99 нет на сервере
gone, _ = rc._alive_partner(g, 2)
check("partner gone -> none", gone is None)
check("marriage cleaned", rc._partner_of(555, 2) is None and rc._partner_of(555, 99) is None)

# Развод
rc._remove_marriage(555, 1, 2)
check("divorce cleans both", rc._partner_of(555, 1) is None and rc._partner_of(555, 2) is None)

# Персистентность
rc._set_marriage(555, 1, 2, time.time())
check("persisted", os.path.exists(os.path.join(storage.DATA_DIR, "marriages.json")))

# Пол и фильтр брака
male = FakeMember(10, [rc.MALE_ROLE_ID])
female = FakeMember(11, [rc.FEMALE_ROLE_ID])
nobody = FakeMember(12, [123])
both = FakeMember(13, [rc.MALE_ROLE_ID, rc.FEMALE_ROLE_ID])

check("gender male", rc._gender(male) == "male")
check("gender female", rc._gender(female) == "female")
check("gender none", rc._gender(nobody) is None)
check("gender both -> none", rc._gender(both) is None)

check("m+f allowed", rc._marriage_block(male, female) == "")
check("f+m allowed", rc._marriage_block(female, male) == "")
check("m+m blocked", "Однополые" in rc._marriage_block(male, male), rc._marriage_block(male, male)[:30])
check("f+f blocked", "Однополые" in rc._marriage_block(female, female))
check("no gender blocked", "верифиц" in rc._marriage_block(male, nobody))

# Юбилеи
check("next milestone 0", rc._next_milestone(0) == 7)
check("next milestone 7", rc._next_milestone(7) == 30)
check("next milestone 30", rc._next_milestone(30) == 100)
check("next milestone 182", rc._next_milestone(182) == 365)
check("next milestone 365", rc._next_milestone(365) == 730)
check("next milestone far", rc._next_milestone(365 * 25) is None)

check("reached empty", rc._reached_milestones(29, []) == [7])
check("reached both", rc._reached_milestones(30, []) == [7, 30])
check("reached skips done", rc._reached_milestones(30, [7]) == [30])
check("reached no dup", rc._reached_milestones(30, [7, 30]) == [])

check("milestone text 7", rc._milestone_text(7) == "неделя вместе")
check("milestone text 100", rc._milestone_text(100) == "100 дней вместе")
check("milestone text 365", rc._milestone_text(365) == "1 год вместе")
check("milestone text 730", rc._milestone_text(730) == "2 года вместе")
check("milestone text 1460", rc._milestone_text(1460) == "4 года вместе")

# Парная комната ищется по паре, порядок не важен
rc._save_rooms(556, {
    "100": {"owner_id": 1, "partner_id": 2, "kind": "couple", "text_channel_id": 101},
    "200": {"owner_id": 3, "kind": "single"},
})
vid, info = rc._couple_room_for(556, 2, 1)
check("couple room found", vid == 100 and info.get("text_channel_id") == 101)
check("single room not couple", rc._couple_room_for(556, 3, 4)[0] is None)
check("stranger no room", rc._couple_room_for(556, 5, 6)[0] is None)


# Без пары комната не создаётся
class SendMember:
    def __init__(self, uid):
        self.id = uid
        self.display_name = "Тест"
        self.sent = []

    async def send(self, **kw):
        self.sent.append(kw)


class NoChannelGuild:
    def __init__(self):
        self.id = 777
        self.created = []

    def get_member(self, uid):
        return None

    def get_channel(self, cid):
        return None


solo = SendMember(42)
solo_guild = NoChannelGuild()
cog_obj = rc.Rooms.__new__(rc.Rooms)
asyncio.run(cog_obj._create_couple_room(solo, solo_guild, {}))
check("no partner -> no room", not solo_guild.created and bool(solo.sent))
refusal_text = solo.sent[0]["embed"].description if solo.sent else ""
check("refusal has marry hint", "marry" in refusal_text.lower(), refusal_text[:40])

# Вьюхи собираются
class _FakeDiscordMember:
    def __init__(self, uid):
        self.id = uid
        self.display_name = f"user{uid}"
        self.mention = f"<@{uid}>"


class _FakeProposalMsg:
    def __init__(self):
        self.edits = []

    async def edit(self, **kw):
        self.edits.append(kw)


prop_session = rc.ProposalSession(
    _FakeDiscordMember(1), _FakeDiscordMember(2), _FakeProposalMsg()
)
pv = rc.ProposalView(prop_session)
check("proposal buttons", len(pv.children) == 2)
check("proposal timeout from session", pv.timeout == rc.ProposalSession.TIMEOUT)

msg_channel = _FakeProposalMsg()
msg_dm = _FakeProposalMsg()
prop_session.attach(msg_channel, pv)
prop_session.attach(msg_dm, rc.ProposalView(prop_session))
asyncio.run(prop_session.expire())
check(
    "expire edits channel+dm",
    len(msg_channel.edits) == 1 and len(msg_dm.edits) == 1,
    f"{len(msg_channel.edits)}/{len(msg_dm.edits)}",
)
check("expire drops buttons", msg_channel.edits[0].get("view") is None)
check("expire embed", "Время вышло" in msg_channel.edits[0]["embed"].title)
asyncio.run(prop_session.expire())
check("expire once", len(msg_channel.edits) == 1 and len(msg_dm.edits) == 1)

# Ответ в ЛС должен править и сообщение в канале, и ЛС
prop_session2 = rc.ProposalSession(
    _FakeDiscordMember(1), _FakeDiscordMember(2), _FakeProposalMsg()
)
m_c, m_d = _FakeProposalMsg(), _FakeProposalMsg()
prop_session2.attach(m_c, rc.ProposalView(prop_session2))
prop_session2.attach(m_d, rc.ProposalView(prop_session2))
prop_session2.answered = True
asyncio.run(prop_session2.edit_all(prop_session2.expire_embed()))
check("edit_all hits both", len(m_c.edits) == 1 and len(m_d.edits) == 1)
check("edit_all strips view", m_d.edits[0].get("view") is None)

src = open("cogs/rooms.py", encoding="utf-8").read()
check("marry sends DM", "await user.send(embed=dm_embed, view=dm_view)" in src)
check(
    "marry falls back to couple panel",
    'cfg.get("couple_panel_id") or cfg.get("panel_channel_id")' in src,
)


# ── Приватность парных комнат: прячем от стаффа ───────────────────────────────

class _Perms:
    def __init__(self, **kw):
        self.__dict__.update(kw)

    def __getattr__(self, item):
        return False


class _PrivRole:
    def __init__(self, rid, name="role", managed=False, default=False, **perms):
        self.id = rid
        self.name = name
        self.mention = f"<@&{rid}>"
        self.managed = managed
        self._default = default
        self.permissions = _Perms(**perms)

    def is_default(self):
        return self._default


class _PrivGuild:
    def __init__(self, roles):
        self.id = 4242
        self.roles = roles


class _PrivChannel:
    def __init__(self):
        self.perms = {}

    async def set_permissions(self, role, **kw):
        self.perms[role.id] = kw


mod_role = _PrivRole(1, "Модератор", manage_messages=True)
helper_role = _PrivRole(2, "Хелпер", moderate_members=True)
admin_role = _PrivRole(3, "Админ", administrator=True)
everyone_role = _PrivRole(0, "@everyone", default=True, manage_messages=True)
bot_role = _PrivRole(5, "Bot", managed=True, administrator=True)
plain_role = _PrivRole(6, "Участник")
priv_guild = _PrivGuild([everyone_role, mod_role, helper_role, admin_role, bot_role, plain_role])

found = {r.id for r in rc._roles_to_hide(priv_guild)}
check("all roles hidden", found == {1, 2, 3, 6}, str(sorted(found)))
check("everyone skipped", 0 not in found)
check("managed bot role skipped", 5 not in found)
check("admin role included", 3 in found)

vch = _PrivChannel()
hidden = asyncio.run(rc._hide_from_staff(vch, priv_guild, voice=True))
check("voice hidden from all roles", hidden == 4, str(hidden))
check(
    "voice deny view+connect",
    vch.perms[1]["view_channel"] is False and vch.perms[1]["connect"] is False,
)
check("plain role also hidden", vch.perms.get(6, {}).get("view_channel") is False)

tch = _PrivChannel()
asyncio.run(rc._hide_from_staff(tch, priv_guild, voice=False))
check(
    "text deny view only",
    tch.perms[3]["view_channel"] is False and "connect" not in tch.perms[3],
)
check("text chat overwrites all roles", "_roles_to_hide(guild)" in src)
check("old rooms re-hidden", "_hide_existing_couple_rooms" in src and "await _hide_existing_couple_rooms" in src)
check("no staff line in couple text", "и стаффу" not in src)


# ── Панель в ЛС: interaction.guild там None, кнопки не должны падать ──────────

class _PanelChannel:
    def __init__(self, guild, cid):
        self.guild = guild
        self.id = cid


class _PanelGuild:
    def __init__(self, gid):
        self.id = gid
        self._channels = {}
        self.members_by_id = {}

    def get_channel(self, cid):
        return self._channels.get(cid)

    def get_member(self, uid):
        return self.members_by_id.get(uid)


class _PanelClient:
    def __init__(self, channels):
        self._channels = {c.id: c for c in channels}

    def get_channel(self, cid):
        return self._channels.get(cid)


class _PanelUser:
    def __init__(self, uid):
        self.id = uid


panel_guild = _PanelGuild(777)
panel_ch = _PanelChannel(panel_guild, 555)
panel_guild._channels[555] = panel_ch
dm_itx = SimpleNamespace(client=_PanelClient([panel_ch]), guild=None, user=_PanelUser(999))
stranger_itx = SimpleNamespace(client=_PanelClient([panel_ch]), guild=None, user=_PanelUser(1000))

orig_owner_of = rc._owner_of
rc._owner_of = lambda gid, chid: 999 if (gid, chid) == (777, 555) else 0
check("dm: channel resolved via cache", rc.RoomPanel._voice_ch(dm_itx, "555") is panel_ch)
check("dm: owner passes", rc.RoomPanel._check_owner(dm_itx, panel_ch) is True)
check("dm: stranger denied", rc.RoomPanel._check_owner(stranger_itx, panel_ch) is False)
check("dm: bad cid no crash", rc.RoomPanel._voice_ch(dm_itx, "not-a-number") is None)
rc._owner_of = orig_owner_of
check("panel helpers avoid interaction.guild", "interaction.client.get_channel" in src)

# ── /setup_couples не должен умирать по 3-секундному таймауту интеракции ──────

i_defer = src.find("await interaction.response.defer(ephemeral=True)")
i_hide = src.find("fixed = await _hide_existing_couple_rooms")
i_followup = src.find("await interaction.followup.send(embed=embed, ephemeral=True)")
check("setup_couples defers first", 0 < i_defer < i_hide, f"defer={i_defer} hide={i_hide}")
check("setup_couples answers via followup", i_hide < i_followup, f"followup={i_followup}")
i_weddings = src.find("# ── Свадьбы")
setup_couples_src = src[i_defer:i_weddings]
check(
    "no direct response in setup_couples tail",
    "interaction.response.send_message" not in setup_couples_src,
)
check("build stamp in footer", 'сборка {BUILD}' in src and 'BUILD = ' in src)

dv = rc.DivorceView(_FakeDiscordMember(1), _FakeDiscordMember(2))
check("divorce buttons", len(dv.children) == 2)

# Ког и команды
import asyncio

import discord
from discord.ext import commands


async def load_cog():
    bt = commands.Bot(command_prefix="!", intents=discord.Intents.default())
    await bt.load_extension("cogs.rooms")
    return bt


bt = asyncio.run(load_cog())
names = {c.name for c in bt.tree.get_commands()}
check("commands", {"my_room", "setup_rooms", "rooms", "marry", "divorce", "marriage", "couples", "setup_couples"} <= names, str(sorted(names)))
check("setup", callable(rc.setup))

# Кириллица не сломана
check("cyrillic ok", "Предложить" in next(c.description for c in bt.tree.get_commands() if c.name == "marry"))

import re
t = open("cogs/rooms.py", encoding="utf-8").read()
cjk = len(re.findall(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]", t))
check("no CJK", cjk == 0, str(cjk))

print(f"\nВсего провалов: {len(fails)}")
if fails:
    print("Провалились: " + ", ".join(fails))
sys.exit(1 if fails else 0)
