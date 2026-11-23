# -*- coding: utf-8 -*-
"""Проверка свадеб и парных комнат bot-rooms без Discord (временная папка data)."""

import os
import sys
import tempfile
import time

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


class FakeMember:
    def __init__(self, uid):
        self.id = uid


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
check("commands", {"my_room", "setup_rooms", "rooms", "marry", "divorce", "marriage", "couples"} <= names, str(sorted(names)))
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
