#!/usr/bin/env python3
"""
tg_handle_finder.py

Generate word-like Telegram usernames (5–6 chars) and authentically check availability
using Telegram MTProto via Telethon (account.checkUsername).

Outputs:
- available.txt  -> only available usernames (one per line)
- checked.txt    -> all checked usernames with status

First run will ask for your phone + login code and save a local session file.

USAGE:
  pip install telethon
  python tg_handle_finder.py --api-id 12345 --api-hash abcdef... --checks 500

Recommended:
  Start small (100–500 checks), skim the outputs, then run again.
"""

import argparse
import asyncio
import random
import re
from pathlib import Path
from typing import Set, List, Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.functions.account import CheckUsernameRequest


# Telegram username rules (letters, numbers, underscore; min length 5; starts with letter)
USERNAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{4,31}$")

VOWELS = "aeiou"
CONSONANTS = "bcdfghjklmnpqrstvwxyz"

ONSET_CLUSTERS = [
    "bl","br","ch","cl","cr","dr","fl","fr","gl","gr","pl","pr","sl","sm","sn","sp","st","str",
    "tr","tw","sh","th","ph","qu","sk","wh","wr"
]

CODA_CLUSTERS = [
    "ck","ct","ft","ld","lk","lm","ln","lp","lt","mp","nd","ng","nk","nt","pt","rd","rk","rm",
    "rn","rp","rt","sk","sp","st","th"
]

BANNED_SUBSTRINGS = {"qj", "jq", "wv", "vw", "zx", "xz", "qh", "hh", "vv", "ww", "yy"}


def normalize(u: str) -> str:
    u = u.strip()
    if u.startswith("@"):
        u = u[1:]
    return u.lower()


def looks_ok(s: str) -> bool:
    s = s.lower()
    if any(b in s for b in BANNED_SUBSTRINGS):
        return False

    # Avoid 3 vowels or 3 consonants in a row
    def is_vowel(c): return c in VOWELS
    run = 1
    for i in range(1, len(s)):
        if is_vowel(s[i]) == is_vowel(s[i - 1]):
            run += 1
            if run >= 3:
                return False
        else:
            run = 1

    # Avoid starting/ending with awkward letters
    if s[0] in "xq" or s[-1] in "xq":
        return False

    return True


def make_syllable() -> str:
    onset = random.choice(ONSET_CLUSTERS) if random.random() < 0.45 else random.choice(CONSONANTS)
    vowel = random.choice(VOWELS)
    coda = ""
    if random.random() < 0.35:
        coda = random.choice(CODA_CLUSTERS) if random.random() < 0.55 else random.choice(CONSONANTS)
    return onset + vowel + coda


def make_name(target_len: int) -> str:
    s = ""
    while len(s) < target_len:
        s += make_syllable()
    s = s[:target_len].lower()
    return s


def generate_candidates(n: int, length_min: int, length_max: int, seed: Optional[int] = None) -> List[str]:
    if seed is not None:
        random.seed(seed)

    results: Set[str] = set()
    tries = 0
    max_tries = n * 80  # plenty to get unique + filtered names

    while len(results) < n and tries < max_tries:
        tries += 1
        L = random.randint(length_min, length_max)
        cand = make_name(L)

        if looks_ok(cand) and USERNAME_RE.match(cand):
            results.add(cand)

    return list(results)


async def check_username(client: TelegramClient, username: str) -> bool:
    """
    Returns True if available, False if taken.
    Raises exceptions on network / flood / other RPC issues.
    """
    u = normalize(username)
    if not USERNAME_RE.match(u):
        # Treat invalid as "not available" for our pipeline
        return False
    return await client(CheckUsernameRequest(u))


async def run_checks(
    client: TelegramClient,
    checks: int,
    length_min: int,
    length_max: int,
    available_path: Path,
    checked_path: Path,
    delay_min: float,
    delay_max: float,
    seed: Optional[int] = None,
) -> None:
    """
    Generate and check `checks` usernames (best-effort), writing results to files.
    """

    # We generate a bit more than needed because some will be duplicates/invalid.
    # Also, we avoid re-checking ones already in checked.txt to save calls.
    already_checked: Set[str] = set()
    if checked_path.exists():
        for line in checked_path.read_text(encoding="utf-8").splitlines():
            # Format: "@name -> STATUS"
            if line.startswith("@"):
                name = line.split(" ", 1)[0][1:].strip()
                if name:
                    already_checked.add(name)

    # Open files in append mode (so you can run multiple times)
    available_f = available_path.open("a", encoding="utf-8")
    checked_f = checked_path.open("a", encoding="utf-8")

    try:
        done = 0
        # Keep generating until we've checked enough new ones
        while done < checks:
            batch_need = min(200, checks - done)
            candidates = generate_candidates(batch_need * 3, length_min, length_max, seed=seed)

            for cand in candidates:
                if done >= checks:
                    break

                cand = normalize(cand)
                if cand in already_checked:
                    continue
                if not USERNAME_RE.match(cand):
                    continue

                # Rate-safe delay between checks
                await asyncio.sleep(random.uniform(delay_min, delay_max))

                try:
                    ok = await check_username(client, cand)
                    if ok:
                        checked_f.write(f"@{cand} -> AVAILABLE\n")
                        checked_f.flush()
                        available_f.write(f"{cand}\n")
                        available_f.flush()
                        print(f"@{cand} -> AVAILABLE ✅ (saved)")
                    else:
                        checked_f.write(f"@{cand} -> TAKEN\n")
                        checked_f.flush()
                        print(f"@{cand} -> TAKEN ❌")

                    already_checked.add(cand)
                    done += 1

                except FloodWaitError as e:
                    # Telegram explicitly tells you how many seconds to wait
                    wait_s = int(getattr(e, "seconds", 0) or 0)
                    msg = f"@{cand} -> FLOOD_WAIT {wait_s}s"
                    checked_f.write(msg + "\n")
                    checked_f.flush()
                    print(msg)
                    # Respect it
                    await asyncio.sleep(wait_s + 1)

                except RPCError as e:
                    msg = f"@{cand} -> RPC_ERROR {type(e).__name__}"
                    checked_f.write(msg + "\n")
                    checked_f.flush()
                    print(msg)
                    # Small backoff
                    await asyncio.sleep(2.0)

                except Exception as e:
                    msg = f"@{cand} -> ERROR {type(e).__name__}"
                    checked_f.write(msg + "\n")
                    checked_f.flush()
                    print(msg)
                    await asyncio.sleep(2.0)

    finally:
        available_f.close()
        checked_f.close()


async def main_async(args) -> None:
    client = TelegramClient(args.session, args.api_id, args.api_hash)
    await client.start()  # first run prompts for phone/code
    try:
        await run_checks(
            client=client,
            checks=args.checks,
            length_min=args.min_len,
            length_max=args.max_len,
            available_path=Path(args.available_out),
            checked_path=Path(args.checked_out),
            delay_min=args.delay_min,
            delay_max=args.delay_max,
            seed=args.seed,
        )
    finally:
        await client.disconnect()


def main():
    ap = argparse.ArgumentParser(description="Generate + check Telegram usernames (word-like) and save available ones.")
    ap.add_argument("--api-id", type=int, required=True, help="Telegram API ID from my.telegram.org")
    ap.add_argument("--api-hash", type=str, required=True, help="Telegram API hash from my.telegram.org")
    ap.add_argument("--session", type=str, default="tg_username_check_session", help="Session file name (no extension)")
    ap.add_argument("--checks", type=int, default=200, help="How many usernames to CHECK this run (keep it reasonable)")
    ap.add_argument("--min", dest="min_len", type=int, default=5, help="Minimum length (>=5)")
    ap.add_argument("--max", dest="max_len", type=int, default=6, help="Maximum length")
    ap.add_argument("--available-out", type=str, default="available.txt", help="Where to save available usernames")
    ap.add_argument("--checked-out", type=str, default="checked.txt", help="Where to log all checked usernames")
    ap.add_argument("--delay-min", type=float, default=0.7, help="Min delay between checks (seconds)")
    ap.add_argument("--delay-max", type=float, default=1.3, help="Max delay between checks (seconds)")
    ap.add_argument("--seed", type=int, default=None, help="Optional RNG seed (repeatable generation)")
    args = ap.parse_args()

    if args.min_len < 5:
        raise SystemExit("Minimum length must be >= 5 (Telegram requirement).")
    if args.max_len < args.min_len:
        raise SystemExit("--max must be >= --min")
    if args.delay_max < args.delay_min:
        raise SystemExit("--delay-max must be >= --delay-min")

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
