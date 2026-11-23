# -*- coding: utf-8 -*-
"""Проверка свадеб и парных комнат bot-rooms без Discord (временная папка data)."""

import os
import sys
import tempfile
import time

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


pv = rc.ProposalView(_FakeDiscordMember(1), _FakeDiscordMember(2))
check("proposal buttons", len(pv.children) == 2)
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
