#!/usr/bin/env python3
"""
Telegram task automation (Telethon).

  python clicker.py             -> one pass over all 3 sections, then exit (GitHub Actions)
  python clicker.py --loop      -> repeat every 30 min (VPS / Oracle server)
  python clicker.py --inspect   -> do NOT click tasks; send the real bot layout to Saved Messages

Env: API_ID, API_HASH, SESSION_STRING (required)
     TARGET_BOT (required), MAX_TASKS (25), LOOP_MINUTES (30), SUMMARY (0/1)
Public repo note: Actions logs are PUBLIC, so this script never prints links/usernames.
"""
import argparse, asyncio, os, random, re, time, traceback
import aiohttp
from telethon import TelegramClient, functions, types, errors
from telethon.sessions import StringSession

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION = os.environ["SESSION_STRING"]
BOT = os.environ["TARGET_BOT"]
MAX_TASKS = int(os.getenv("MAX_TASKS", "25"))
LOOP_MIN = int(os.getenv("LOOP_MINUTES", "30"))
SUMMARY = os.getenv("SUMMARY", "0") == "1"

NO_ADS = re.compile(r"no more ads|no ads (are )?available|no tasks", re.I)
REWARD = re.compile(r"earn|reward|credited|added to|success|congrat|\+\s*\d", re.I)
UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36")


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


# ---------- helpers ----------
def flat(m):
    return [b for row in (m.buttons or []) for b in row]

def url_button(m):
    return next((b for b in flat(m) if b.url), None)

def find_btn(m, pattern):
    return next((b for b in flat(m) if not b.url and re.search(pattern, b.text or "", re.I)), None)

async def snap(c):
    return {m.id: (m.raw_text or "") for m in await c.get_messages(BOT, limit=6)}

async def wait_change(c, before, timeout=20, pred=None):
    """Wait for a NEW bot message or an EDITED one (text changed)."""
    end = time.time() + timeout
    while time.time() < end:
        for m in sorted(await c.get_messages(BOT, limit=6), key=lambda x: x.id):
            if m.out:
                continue
            if before.get(m.id) != (m.raw_text or "") and (pred is None or pred(m)):
                return m
        await asyncio.sleep(1.5)
    return None

async def notify(c, text):
    try:
        await c.send_message("me", ("⚠️ Runner: " + text)[:3900])
    except Exception as e:  # noqa
        log(f"could not write to Saved Messages: {e}")

def dump(m):
    lines = [(m.raw_text or "(no text)")[:800], "--- buttons ---"]
    for b in flat(m):
        kind = "URL" if b.url else "callback/keyboard"
        lines.append(f"[{b.text}] ({kind})" + (f" -> {b.url}" if b.url else ""))
    return "\n".join(lines)

async def find_label(c, key):
    """Real reply-keyboard text (with emoji) of the menu button containing `key`."""
    for attempt in range(2):
        for m in await c.get_messages(BOT, limit=20):
            for b in flat(m):
                if key.lower() in (b.text or "").lower():
                    return b.text
        if attempt == 0:
            await c.send_message(BOT, "/start")
            await asyncio.sleep(3)
    return key


async def http_visit(url):
    """Open the ad link like a phone browser (follows redirects)."""
    try:
        async with aiohttp.ClientSession(headers={"User-Agent": UA}) as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=40), allow_redirects=True) as r:
                await r.read()
                return r.status
    except Exception as e:
        log(f"visit error: {type(e).__name__}")
        return None


def timer_from(text, default=45):
    m = re.search(r"(\d{1,3})\s*(?:sec|seconds|s)\b", text or "", re.I)
    return min(int(m.group(1)) + 6, 120) if m else default


async def confirm_reward(c, before, timeout=25):
    m = await wait_change(c, before, timeout, pred=lambda x: bool(REWARD.search(x.raw_text or "")))
    return m is not None


# ---------- task handlers: return "ok" | "fail" ----------
async def do_link(c, m):
    b = url_button(m)
    if not b:
        await notify(c, "Visit Links: no URL button.\n" + dump(m)); return "fail"
    wait = timer_from(m.raw_text)
    before = await snap(c)
    status = await http_visit(b.url)
    log(f"link opened (http {status}), waiting {wait}s")
    await asyncio.sleep(wait)
    if await confirm_reward(c, before, 20):
        return "ok"
    chk = find_btn(m, r"check|verify|done|claim|continue|visited")
    if chk:
        before = await snap(c)
        try: await chk.click()
        except Exception: pass
        if await confirm_reward(c, before, 20):
            return "ok"
    log("link: reward not confirmed")
    return "fail"


async def do_bot(c, m):
    b = url_button(m)
    if not b:
        await notify(c, "Message Bots: no URL button.\n" + dump(m)); return "fail"
    mt = re.match(r"https?://t\.me/([A-Za-z0-9_]+)(?:\?start=(\S+))?", b.url)
    if not mt:
        await notify(c, "Message Bots: unexpected link format"); return "fail"
    user, param = mt.group(1), mt.group(2)
    ent = await c.get_entity(user)
    before_t = {x.id: (x.raw_text or "") for x in await c.get_messages(ent, limit=4)}
    if param:
        await c(functions.messages.StartBotRequest(bot=ent, peer=ent, start_param=param))
    else:
        await c.send_message(ent, "/start")
    reply, end = None, time.time() + 30
    while time.time() < end and not reply:
        await asyncio.sleep(2)
        for x in sorted(await c.get_messages(ent, limit=4), key=lambda x: x.id):
            if not x.out and before_t.get(x.id) != (x.raw_text or ""):
                reply = x; break
    if not reply:
        log("target bot did not answer"); return "fail"
    before = await snap(c)
    await c.forward_messages(BOT, reply)
    if await confirm_reward(c, before, 30):
        return "ok"
    log("bot: reward not confirmed")
    return "fail"


async def do_join(c, m):
    b = url_button(m)
    if not b:
        await notify(c, "Join Chats: no URL button.\n" + dump(m)); return "fail"
    inv = re.search(r"t\.me/(?:\+|joinchat/)([\w-]+)", b.url)
    pub = re.search(r"t\.me/([A-Za-z0-9_]{4,})", b.url)
    try:
        if inv:
            try:
                await c(functions.messages.ImportChatInviteRequest(inv.group(1)))
            except errors.UserAlreadyParticipantError:
                pass
        elif pub:
            ent = await c.get_entity(pub.group(1))
            if isinstance(ent, types.User) and ent.bot:
                await c.send_message(ent, "/start")
            else:
                await c(functions.channels.JoinChannelRequest(ent))
        else:
            return "fail"
    except errors.FloodWaitError as e:
        if e.seconds <= 120:
            await asyncio.sleep(e.seconds + 2)
        else:
            await notify(c, f"FloodWait {e.seconds}s while joining - stopping Join Chats"); raise
        return "fail"
    except (errors.InviteHashExpiredError, errors.InviteHashInvalidError,
            errors.UsernameInvalidError, errors.UsernameNotOccupiedError,
            errors.ChannelPrivateError, errors.UserBannedInChannelError):
        return "fail"
    except errors.ChannelsTooMuchError:
        await notify(c, "Too many joined chats (limit reached). Leave some chats manually.")
        raise
    await asyncio.sleep(random.uniform(4, 8))
    done = find_btn(m, r"joined|done|check|verify|confirm")
    if not done:
        await notify(c, "Join Chats: no 'Joined' button.\n" + dump(m)); return "fail"
    before = await snap(c)
    try: await done.click()
    except Exception as e: log(f"click error {type(e).__name__}")
    return "ok" if await confirm_reward(c, before, 25) else "fail"


SECTIONS = [("Visit Links", do_link), ("Message Bots", do_bot), ("Join Chats", do_join)]


async def run_section(c, key, handler):
    ok = fails = 0
    for _ in range(MAX_TASKS):
        label = await find_label(c, key)
        before = await snap(c)
        await c.send_message(BOT, label)
        m = await wait_change(c, before, 20)
        if m is None:
            log(f"{key}: no answer from bot"); break
        if NO_ADS.search(m.raw_text or ""):
            log(f"{key}: no tasks"); break
        res = await handler(c, m)
        if res == "ok":
            ok += 1; fails = 0
        else:
            fails += 1
            skip = find_btn(m, r"skip")
            if skip:
                try: await skip.click()
                except Exception: pass
            if fails >= 3:
                log(f"{key}: 3 failures in a row, stopping section"); break
        await asyncio.sleep(random.uniform(3, 8))
    return ok


async def one_pass(c):
    totals = {}
    for key, handler in SECTIONS:
        try:
            totals[key] = await run_section(c, key, handler)
        except errors.FloodWaitError as e:
            await notify(c, f"{key}: FloodWait {e.seconds}s"); totals[key] = "flood"
        except Exception:
            await notify(c, f"{key} crashed:\n{traceback.format_exc()[-1500:]}")
            totals[key] = "error"
    log(f"done: {totals}")
    if SUMMARY:
        await c.send_message("me", f"✅ runner pass: {totals}")


async def inspect(c):
    await c.send_message(BOT, "/start"); await asyncio.sleep(4)
    for key, _ in SECTIONS:
        before = await snap(c)
        await c.send_message(BOT, await find_label(c, key))
        m = await wait_change(c, before, 20)
        await c.send_message("me", f"🔎 {key}:\n" + (dump(m) if m else "(no answer)"))
    log("inspect done - read Saved Messages")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--inspect", action="store_true")
    a = ap.parse_args()
    async with TelegramClient(StringSession(SESSION), API_ID, API_HASH) as c:
        if not await c.is_user_authorized():
            raise SystemExit("SESSION_STRING invalid/expired - regenerate it")
        if a.inspect:
            await inspect(c); return
        while True:
            try:
                await one_pass(c)
            except Exception:
                await notify(c, "fatal:\n" + traceback.format_exc()[-1500:])
            if not a.loop:
                break
            await asyncio.sleep(LOOP_MIN * 60 + random.uniform(-60, 60))

if __name__ == "__main__":
    asyncio.run(main())
