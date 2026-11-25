# -*- coding: utf-8 -*-
"""Проверка РП-команд bot-rooms без Discord."""

import asyncio
import os
import re
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, r"C:\Users\tomoe\Downloads\bot-rooms")

import discord
from discord import app_commands
from discord.ext import commands

from cogs import rp

fails = []


def check(name, cond, extra=""):
    print(("OK   " if cond else "FAIL ") + name + (f"  {extra}" if extra else ""))
    if not cond:
        fails.append(name)


EXPECTED = {
    "обнять", "поцеловать", "погладить", "приобнять", "угостить", "помахать", "чмок",
    "пнуть", "шлёпнуть", "стукнуть", "укусить", "ткнуть", "пощекотать", "пять", "держаться",
}

check("actions", set(rp.ACTIONS) == EXPECTED, str(sorted(rp.ACTIONS)))
for key, (filename, endpoint, title, color, variants) in rp.ACTIONS.items():
    check(f"endpoint {key}", bool(endpoint) and " " not in endpoint)
    check(f"variants {key}", len(variants) >= 2)
    check(f"placeholders {key}", all("{a}" in v and "{b}" in v for v in variants))
    check(f"title {key}", bool(title))

# ── сборка эмбеда ─────────────────────────────────────────────────────────────

embed, filename = rp.build_embed("обнять", "Маша", "Петя")
check("embed text", "Маша" in embed.description and "Петя" in embed.description)
check("no leftover placeholders", "{a}" not in embed.description and "{b}" not in embed.description)
check("embed footer", "Roet" in (embed.footer.text or ""))
check("ordinary color", embed.color.value != rp.HEART_COLOR)

embed_sp, _ = rp.build_embed("обнять", "Маша", "Петя", spouse=True, target_mention="<@222>")
check("spouse pink", embed_sp.color.value == rp.HEART_COLOR)
check("spouse line", "💞" in embed_sp.description or "❤️" in embed_sp.description)
check("spouse mentions partner", "<@222>" in embed_sp.description)
check(
    "spouse no leftover placeholders",
    "{a}" not in embed_sp.description and "{b}" not in embed_sp.description,
)
check("spouse lines need mention", all("{b}" in line for line in rp.SPOUSE_LINES))
check("playful lines need mention", all("{b}" in line for line in rp.PLAYFUL_SPOUSE_LINES))
check(
    "spouse line without mention stays readable",
    "Петя" in rp.build_embed("обнять", "Маша", "Петя", spouse=True)[0].description,
)

embed_both, _ = rp.build_embed(
    "обнять", "Маша", "Петя", spouse=True,
    actor_mention="<@111>", target_mention="<@222>",
)
action_line = embed_both.description.split("\n")[0]
check(
    "both mentions in action line",
    "<@111>" in action_line and "<@222>" in action_line,
)
check(
    "compliment repeats both mentions",
    "<@111>" in embed_both.description.split("\n")[-1]
    and "<@222>" in embed_both.description.split("\n")[-1],
)
check("names replaced by mentions", "Маша" not in embed_both.description)

embed_play, _ = rp.build_embed("пнуть", "Маша", "Петя", spouse=True, target_mention="<@222>")
check(
    "playful spouse line",
    any(line.format(a="Маша", b="<@222>") in embed_play.description for line in rp.PLAYFUL_SPOUSE_LINES),
)
check(
    "romantic spouse line",
    any(line.format(a="Маша", b="<@222>") in embed_sp.description for line in rp.SPOUSE_LINES),
)
check("playful set", rp.PLAYFUL <= set(rp.ACTIONS))

# ── разбор ответа nekos.best ──────────────────────────────────────────────────

check(
    "payload ok",
    rp.parse_nekos_payload(
        {"results": [{"url": "https://nekos.best/api/v2/hug/abc.gif"}]}
    ) == "https://nekos.best/api/v2/hug/abc.gif",
)
check("payload empty", rp.parse_nekos_payload({}) == "")
check("payload not dict", rp.parse_nekos_payload(None) == "")
check("payload no url", rp.parse_nekos_payload({"results": [{"anime_name": "x"}]}) == "")
check(
    "payload http rejected",
    rp.parse_nekos_payload({"results": [{"url": "http://evil.example/x.gif"}]}) == "",
)

# ── общая тестовая папка картинок ────────────────────────────────────────────

tmp = tempfile.mkdtemp(prefix="rp_check_")
rp.RP_DIR = tmp
with open(os.path.join(tmp, "hug.gif"), "wb") as f:
    f.write(b"GIF89a" + b"0" * 64)
with open(os.path.join(tmp, "pat.png"), "wb") as f:
    f.write(b"\x89PNG" + b"0" * 64)

# ── пол пары и выбор картинки ────────────────────────────────────────────────


class _Role:
    def __init__(self, rid):
        self.id = rid


class _Gendered:
    def __init__(self, *role_ids):
        self.roles = [_Role(r) for r in role_ids]
        self.id = 1
        self.display_name = "x"


male_id = 1573422846485594193
female_id = 1573422843574886453
unknown_id = 999

check("suffix mm", rp.pair_suffix(_Gendered(male_id), _Gendered(male_id)) == "mm")
check("suffix ff", rp.pair_suffix(_Gendered(female_id), _Gendered(female_id)) == "ff")
check("suffix mf", rp.pair_suffix(_Gendered(male_id), _Gendered(female_id)) == "mf")
check("suffix mf reversed", rp.pair_suffix(_Gendered(female_id), _Gendered(male_id)) == "mf")
check("suffix no gender", rp.pair_suffix(_Gendered(unknown_id), _Gendered(male_id)) == "")
check("suffix roles missing", rp.pair_suffix(object(), object()) == "")

with open(os.path.join(tmp, "hug_mm.gif"), "wb") as f:
    f.write(b"GIF89a" + b"0" * 64)

check(
    "gender file wins",
    os.path.basename(rp.find_local_image("hug.gif", "mm")) == "hug_mm.gif",
)
check(
    "gender fallback to generic",
    os.path.basename(rp.find_local_image("hug.gif", "ff")) == "hug.gif",
)
check(
    "no suffix uses generic",
    os.path.basename(rp.find_local_image("hug.gif", "")) == "hug.gif",
)
check("missing image", rp.find_local_image("nope.gif", "mm") == "")

# ── рантайм: локальный файл важнее API ────────────────────────────────────────


class _Sent:
    def __init__(self):
        self.calls = []

    async def send_message(self, content=None, **kw):
        self.calls.append({"content": content, **kw})

    def is_done(self):
        return False


class _FakeInteraction:
    def __init__(self, uid=1, name="Маша"):
        self.response = _Sent()
        self.guild = None
        self.guild_id = 4242
        self.user = SimpleNamespace(
            id=uid, display_name=name, mention=f"<@{uid}>"
        )


class _FakeMember:
    def __init__(self, uid=2, name="Петя", bot=False):
        self.id = uid
        self.display_name = name
        self.bot = bot
        self.mention = f"<@{uid}>"


fake = rp.RP(None)
rp.GLOBAL_COOLDOWN = 0  # в остальных тестах тормоз не мешает


async def _no_api(_action):
    raise AssertionError("fetch_image_url не должен вызываться при локальном файле")


orig_fetch = rp.fetch_image_url
rp.fetch_image_url = _no_api
itx = _FakeInteraction()
target_member = _FakeMember()
asyncio.run(fake._run(itx, "обнять", target_member))
sent = itx.response.calls[0]
check("local file attached", "file" in sent and sent["file"].filename == "hug.gif")
check("local file embed image", sent["embed"].image.url == "attachment://hug.gif")
check("local file auto-delete", sent.get("delete_after") == rp.DELETE_AFTER)
check(
    "mentions limited to participants",
    isinstance(sent.get("allowed_mentions"), discord.AllowedMentions)
    and {u.id for u in sent["allowed_mentions"].users} == {1, target_member.id}
    and sent["allowed_mentions"].everyone is False,
)
check(
    "both mentioned in action",
    "<@1>" in sent["embed"].description and "<@2>" in sent["embed"].description,
)

# Супруги: розовый цвет + комплимент с упоминанием
orig_is_spouse = fake._is_spouse
fake._is_spouse = lambda *_a: True
itx_sp = _FakeInteraction(uid=1, name="Маша")
asyncio.run(fake._run(itx_sp, "обнять", _FakeMember(uid=2, name="Петя")))
sent_sp = itx_sp.response.calls[0]
check("spouse runtime pink", sent_sp["embed"].color.value == rp.HEART_COLOR)
check("spouse runtime mention", "<@2>" in sent_sp["embed"].description)
fake._is_spouse = orig_is_spouse

# API-фолбэк: файла нет — берём ссылку


async def _fake_api(_action):
    return "https://nekos.best/api/v2/pat/xyz.gif"


itx_png = _FakeInteraction()
asyncio.run(fake._run(itx_png, "погладить", _FakeMember()))
sent_png = itx_png.response.calls[0]
check("local png attached", sent_png["file"].filename == "pat.png")
check("local png embed image", sent_png["embed"].image.url == "attachment://pat.png")

rp.fetch_image_url = _fake_api
itx2 = _FakeInteraction()
asyncio.run(fake._run(itx2, "чмок", _FakeMember()))
sent2 = itx2.response.calls[0]
check("api image used", sent2["embed"].image.url == "https://nekos.best/api/v2/pat/xyz.gif")
check("api no file", "file" not in sent2)
check("api auto-delete", sent2.get("delete_after") == rp.DELETE_AFTER)

# API молчит — текстом, без падения


async def _dead_api(_action):
    return ""


rp.fetch_image_url = _dead_api
itx3 = _FakeInteraction()
asyncio.run(fake._run(itx3, "угостить", _FakeMember()))
sent3 = itx3.response.calls[0]
check("no api still works", sent3["embed"].image.url is None and "embed" in sent3)
check("no api auto-delete", sent3.get("delete_after") == rp.DELETE_AFTER)

rp.fetch_image_url = orig_fetch

# Общий тормоз: два РП-действия подряд одним человеком
rp.GLOBAL_COOLDOWN = 10.0
fake._last_use.clear()
asyncio.run(fake._run(_FakeInteraction(uid=7), "обнять", _FakeMember(uid=8, name="Аня")))
itx_rate = _FakeInteraction(uid=7)
asyncio.run(fake._run(itx_rate, "чмок", _FakeMember(uid=8, name="Аня")))
check(
    "global cooldown blocks spam",
    itx_rate.response.calls
    and itx_rate.response.calls[0].get("ephemeral") is True
    and "пауза" in str(itx_rate.response.calls[0].get("content")),
)
rp.GLOBAL_COOLDOWN = 0
fake._last_use.clear()

# Бот и сам себя — отказ, эфемерно
itx4 = _FakeInteraction()
asyncio.run(fake._run(itx4, "обнять", _FakeMember(bot=True)))
check("bot refused", itx4.response.calls[0].get("ephemeral") is True)
itx5 = _FakeInteraction(uid=2)
asyncio.run(fake._run(itx5, "обнять", _FakeMember(uid=2)))
check("self refused", itx5.response.calls[0].get("ephemeral") is True)

# Кулдаун — понятный текст, а не «приложение не отвечает»
cooldown = app_commands.Cooldown(1, 3.0)
itx6 = _FakeInteraction()
asyncio.run(fake.cog_app_command_error(itx6, app_commands.CommandOnCooldown(cooldown, 2.4)))
check(
    "cooldown message",
    itx6.response.calls and "подождут" in str(itx6.response.calls[0].get("content")),
)

# ── регистрация команд ────────────────────────────────────────────────────────


async def load_cog():
    bt = commands.Bot(command_prefix="!", intents=discord.Intents.default())
    await bt.load_extension("cogs.rp")
    return bt


bt = asyncio.run(load_cog())
names = {c.name for c in bt.tree.get_commands()}
check("commands registered", names == EXPECTED, str(sorted(names)))

src = open(os.path.join(os.path.dirname(rp.__file__), "rp.py"), encoding="utf-8").read()
check("cooldowns", src.count("cooldown(1, 3.0") == len(EXPECTED))
check("delete after 5 min", rp.DELETE_AFTER == 300)
check("all sends auto-delete", src.count("delete_after=DELETE_AFTER") == 2, str(src.count("delete_after=DELETE_AFTER")))
check("global cooldown wired", "_rate_ok" in src and "GLOBAL_COOLDOWN" in src)
check("cyrillic ok", "Обнимашки" in src)
cjk = len(re.findall(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]", src))
check("no CJK", cjk == 0, str(cjk))

print(f"\nВсего провалов: {len(fails)}")
if fails:
    print("Провалились: " + ", ".join(fails))
sys.exit(1 if fails else 0)
