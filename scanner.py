#!/usr/bin/env python3
"""
tg_handle_finder.py

Generate word-like Telegram usernames and authentically check availability
using Telegram MTProto via Telethon (account.checkUsername).

Outputs:
- available.txt  -> only available usernames (one per line)
- checked.txt    -> all checked usernames with status

First run will ask for your phone + login code and save a local session file.

Install:
  pip install telethon

Run (CLI):
  python tg_handle_finder.py --api-id 12345 --api-hash abcdef... --checks 300

Run (Interactive):
  python tg_handle_finder.py
"""

import argparse
import asyncio
import random
import re
import sys
from pathlib import Path
from typing import Set, List, Optional, Tuple

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


def sanitize_required_substring(s: str) -> str:
    """
    Keep only [a-z0-9_] and lowercase.
    Telegram usernames can't start with a digit, so we'll validate later.
    """
    s = normalize(s)
    s = re.sub(r"[^a-z0-9_]", "", s)
    return s


def looks_ok(s: str) -> bool:
    """
    Keep the original "word-like" filter, but ignore digits/underscores for vowel/consonant runs.
    """
    s = s.lower()
    if any(b in s for b in BANNED_SUBSTRINGS):
        return False

    # Strip digits/underscores for the run check so numbers don't break it
    letters_only = re.sub(r"[^a-z]", "", s)
    if not letters_only:
        return False

    def is_vowel(c): return c in VOWELS
    run = 1
    for i in range(1, len(letters_only)):
        if is_vowel(letters_only[i]) == is_vowel(letters_only[i - 1]):
            run += 1
            if run >= 3:
                return False
        else:
            run = 1

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
    """
    Word-ish letters-only base.
    """
    if target_len <= 0:
        return ""
    s = ""
    while len(s) < target_len:
        s += make_syllable()
    return s[:target_len].lower()


def pick_length_from_choice(choice: str) -> Tuple[int, int]:
    """
    A: 5
    B: 6
    C: 6-8
    D: 8-10
    E: 10+
    F: Doesn't matter.
    """
    choice = choice.strip().upper()
    if choice == "A":
        return (5, 5)
    if choice == "B":
        return (6, 6)
    if choice == "C":
        return (6, 8)
    if choice == "D":
        return (8, 10)
    if choice == "E":
        return (10, 32)
    if choice == "F":
        return (5, 32)
    raise ValueError("Invalid length choice. Use A/B/C/D/E/F.")


def pick_digits_from_choice(choice: str, length_min: int, length_max: int) -> Tuple[int, int]:
    """
    A: 0
    B: 1
    C: 2
    D: 3
    E: 4+
    """
    choice = choice.strip().upper()
    if choice == "A":
        return (0, 0)
    if choice == "B":
        return (1, 1)
    if choice == "C":
        return (2, 2)
    if choice == "D":
        return (3, 3)
    if choice == "E":
        # 4+ but username length max is 32, and too many digits kills word vibe.
        # We'll allow 4..min(8, length_max-1) by default.
        upper = min(8, max(4, length_max - 1))
        return (4, upper)
    raise ValueError("Invalid digit choice. Use A/B/C/D/E.")


def insert_substring(base: str, required: str) -> str:
    if not required:
        return base
    if len(required) > len(base):
        # if required longer than base, we'll just return required and let caller handle length
        return required
    pos = random.randint(0, len(base) - len(required))
    return base[:pos] + required + base[pos + len(required):]


def insert_digits(s: str, digits_count: int) -> str:
    """
    Insert digits as extra characters at random positions (not at index 0),
    increasing total length by digits_count.
    """
    if digits_count <= 0:
        return s

    # Ensure there is at least one char to insert after
    if not s:
        return s

    for _ in range(digits_count):
        d = str(random.randint(0, 9))
        pos = random.randint(1, len(s))  # never 0
        s = s[:pos] + d + s[pos:]
    return s


def build_candidate(
    length_min: int,
    length_max: int,
    digits_min: int,
    digits_max: int,
    required: str,
) -> Optional[str]:
    """
    Build a single candidate satisfying:
    - total length in [length_min, length_max]
    - digits count in [digits_min, digits_max]
    - includes required substring in order (contiguous)
    - starts with a letter
    """
    L = random.randint(length_min, length_max)

    # digits count must not exceed L-1 because first char must be a letter and we want at least 1 letter
    dcount = random.randint(digits_min, digits_max)
    dcount = min(dcount, max(0, L - 1))

    required = sanitize_required_substring(required)
    if required and not re.fullmatch(r"[a-z0-9_]+", required):
        return None

    # total length = base_len + dcount
    base_len = L - dcount
    if base_len < 1:
        return None

    # required must fit inside base (because we insert digits after)
    if required and len(required) > base_len:
        return None

    # create letters-only base, then force required substring into it
    base = make_name(base_len)
    if required:
        base = insert_substring(base, required)

    # ensure first char is a letter (Telegram requirement)
    if not base or not base[0].isalpha():
        return None

    # insert digits as extra chars (not at front)
    cand = insert_digits(base, dcount)

    cand = normalize(cand)

    if not USERNAME_RE.match(cand):
        return None
    if not looks_ok(cand):
        return None

    return cand


def generate_candidates_custom(
    n: int,
    length_min: int,
    length_max: int,
    digits_min: int,
    digits_max: int,
    required: str,
    seed: Optional[int] = None,
) -> List[str]:
    if seed is not None:
        random.seed(seed)

    results: Set[str] = set()
    tries = 0
    max_tries = n * 200  # higher because constraints can be tight

    while len(results) < n and tries < max_tries:
        tries += 1
        cand = build_candidate(length_min, length_max, digits_min, digits_max, required)
        if cand:
            results.add(cand)

    return list(results)


async def check_username(client: TelegramClient, username: str) -> bool:
    u = normalize(username)
    if not USERNAME_RE.match(u):
        return False
    return await client(CheckUsernameRequest(u))


async def run_checks(
    client: TelegramClient,
    checks: int,
    length_min: int,
    length_max: int,
    digits_min: int,
    digits_max: int,
    required: str,
    available_path: Path,
    checked_path: Path,
    delay_min: float,
    delay_max: float,
    seed: Optional[int] = None,
) -> None:
    already_checked: Set[str] = set()
    if checked_path.exists():
        for line in checked_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("@"):
                name = line.split(" ", 1)[0][1:].strip()
                if name:
                    already_checked.add(name)

    available_f = available_path.open("a", encoding="utf-8")
    checked_f = checked_path.open("a", encoding="utf-8")

    try:
        done = 0
        while done < checks:
            batch_need = min(200, checks - done)

            # Generate extra to handle duplicates and "already_checked"
            candidates = generate_candidates_custom(
                n=batch_need * 5,
                length_min=length_min,
                length_max=length_max,
                digits_min=digits_min,
                digits_max=digits_max,
                required=required,
                seed=seed,
            )

            # Shuffle so results aren’t grouped by generation order
            random.shuffle(candidates)

            for cand in candidates:
                if done >= checks:
                    break

                cand = normalize(cand)
                if cand in already_checked:
                    continue
                if not USERNAME_RE.match(cand):
                    continue

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
                    wait_s = int(getattr(e, "seconds", 0) or 0)
                    msg = f"@{cand} -> FLOOD_WAIT {wait_s}s"
                    checked_f.write(msg + "\n")
                    checked_f.flush()
                    print(msg)
                    await asyncio.sleep(wait_s + 1)

                except RPCError as e:
                    msg = f"@{cand} -> RPC_ERROR {type(e).__name__}"
                    checked_f.write(msg + "\n")
                    checked_f.flush()
                    print(msg)
                    await asyncio.sleep(2.0)

                except Exception as e:
                    msg = f"@{cand} -> ERROR {type(e).__name__}"
                    checked_f.write(msg + "\n")
                    checked_f.flush()
                    print(msg)
                    await asyncio.sleep(2.0)

            # If generation got too constrained and we didn’t progress, relax by increasing tries via next loop.
            if done < checks and not candidates:
                print("No candidates generated under current constraints. Try relaxing options.")
                break

    finally:
        available_f.close()
        checked_f.close()


def prompt_nonempty(label: str) -> str:
    while True:
        v = input(label).strip()
        if v:
            return v


def prompt_choice(label: str, allowed: Set[str]) -> str:
    allowed_up = {a.upper() for a in allowed}
    while True:
        v = input(label).strip().upper()
        if v in allowed_up:
            return v
        print(f"Invalid choice. Allowed: {', '.join(sorted(allowed_up))}")


def prompt_int(label: str, default: int, min_v: int, max_v: int) -> int:
    while True:
        raw = input(f"{label} (default {default}): ").strip()
        if raw == "":
            return default
        try:
            x = int(raw)
        except ValueError:
            print("Enter a valid integer.")
            continue
        if x < min_v or x > max_v:
            print(f"Enter a number between {min_v} and {max_v}.")
            continue
        return x


def interactive_config(args) -> None:
    """
    Fill missing args interactively.
    """
    if args.api_id is None:
        args.api_id = int(prompt_nonempty("Enter api_id (number from my.telegram.org): "))
    if args.api_hash is None:
        args.api_hash = prompt_nonempty("Enter api_hash (string from my.telegram.org): ")

    print("\nChoose username length:")
    print("  A: 5")
    print("  B: 6")
    print("  C: 6-8")
    print("  D: 8-10")
    print("  E: 10+")
    print("  F: Doesn't matter")
    len_choice = prompt_choice("Select A/B/C/D/E/F: ", {"A","B","C","D","E","F"})
    length_min, length_max = pick_length_from_choice(len_choice)

    print("\nHow many numbers (digits) in the username?")
    print("  A: 0")
    print("  B: 1")
    print("  C: 2")
    print("  D: 3")
    print("  E: 4+")
    dig_choice = prompt_choice("Select A/B/C/D/E: ", {"A","B","C","D","E"})
    digits_min, digits_max = pick_digits_from_choice(dig_choice, length_min, length_max)

    required = input("\nRequired substring (optional, leave empty for none): ").strip()

    checks = prompt_int("\nHow many usernames to CHECK this run?", default=args.checks, min_v=1, max_v=20000)

    args.min_len = length_min
    args.max_len = length_max
    args.digits_min = digits_min
    args.digits_max = digits_max
    args.required = required
    args.checks = checks


async def main_async(args) -> None:
    client = TelegramClient(args.session, args.api_id, args.api_hash)
    await client.start()
    try:
        await run_checks(
            client=client,
            checks=args.checks,
            length_min=args.min_len,
            length_max=args.max_len,
            digits_min=args.digits_min,
            digits_max=args.digits_max,
            required=args.required,
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
    ap.add_argument("--api-id", type=int, default=None, help="Telegram API ID from my.telegram.org")
    ap.add_argument("--api-hash", type=str, default=None, help="Telegram API hash from my.telegram.org")
    ap.add_argument("--session", type=str, default="tg_username_check_session", help="Session file name (no extension)")
    ap.add_argument("--checks", type=int, default=200, help="How many usernames to CHECK this run")
    ap.add_argument("--available-out", type=str, default="available.txt", help="Where to save available usernames")
    ap.add_argument("--checked-out", type=str, default="checked.txt", help="Where to log all checked usernames")
    ap.add_argument("--delay-min", type=float, default=0.7, help="Min delay between checks (seconds)")
    ap.add_argument("--delay-max", type=float, default=1.3, help="Max delay between checks (seconds)")
    ap.add_argument("--seed", type=int, default=None, help="Optional RNG seed (repeatable generation)")

    # These are set via interactive config unless you provide them
    ap.add_argument("--min", dest="min_len", type=int, default=None, help="Min length (if bypassing interactive)")
    ap.add_argument("--max", dest="max_len", type=int, default=None, help="Max length (if bypassing interactive)")
    ap.add_argument("--digits-min", type=int, default=None, help="Min digits (if bypassing interactive)")
    ap.add_argument("--digits-max", type=int, default=None, help="Max digits (if bypassing interactive)")
    ap.add_argument("--required", type=str, default="", help="Required substring (if bypassing interactive)")

    args = ap.parse_args()

    if args.delay_max < args.delay_min:
        raise SystemExit("--delay-max must be >= --delay-min")

    # If any of the generation settings are missing, ask interactively
    if (
        args.api_id is None
        or args.api_hash is None
        or args.min_len is None
        or args.max_len is None
        or args.digits_min is None
        or args.digits_max is None
    ):
        interactive_config(args)

    # Final validations
    if args.min_len < 5:
        raise SystemExit("Minimum length must be >= 5 (Telegram requirement).")
    if args.max_len < args.min_len:
        raise SystemExit("--max must be >= --min")
    if args.digits_min < 0 or args.digits_max < args.digits_min:
        raise SystemExit("Digit range invalid.")
    if args.digits_max > 31:
        raise SystemExit("Too many digits.")
    if args.digits_min > (args.max_len - 1):
        raise SystemExit("Digits too high for chosen length (username must start with a letter).")

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
