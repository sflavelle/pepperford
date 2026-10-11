#!/usr/bin/env python3
"""
Self-check for the game-handler protocol. Not a handler module (leading `_` keeps it out of
auto-discovery on purpose).

    python -m cmds.ap_scripts.game_handlers._selfcheck                  # registry + hook conformance
    python -m cmds.ap_scripts.game_handlers._selfcheck --corpus FILE.jsonl
                                                                       # behaviour-preservation corpus

The corpus mode reads an archived #item-log export and asks a migration question rather than a
unit-test question: *every* posted line whose item carried a filler/currency icon is a line the
default rule would have suppressed, so some handler (or some still-inline rule) must explain it.
Anything unexplained is either a rule that has not been migrated yet or a behaviour change.

Exit code is non-zero if any check fails, so this can gate a commit.
"""

from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
import sys
import types

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from cmds.ap_scripts.game_handlers import (  # noqa: E402
    HOOKS,
    PROTOCOL_VERSION,
    ClassificationProposal,
    HandlerContext,
    PostDecision,
    SendEvent,
    describe,
    dispatch,
    get_handler_fuzzy,
    get_handlers,
    implements_of,
    register_handler,
)

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("handler_selfcheck")

SUPPRESSED_ICONS = {
    "<:filler:1461179676365164729>": "filler",
    "<:coin:1469865094665207879>": "currency",
}
V2_HOOKS = frozenset(
    {"resolve_conditional_progression", "should_post_item_send", "on_item_send"}
)
POSTED_LINE = re.compile(
    r"^(?P<sender>.+?) sent \*\*(?P<icon><:[a-z]+:\d+>)?\s*(?P<item>.+?)\*\* to "
    r"\*\*(?P<receiver>.+?)\*\* \((?P<loc>.+)\)$"
)


def fixture_ctx(game: str = "Fixture Game", settings: dict | None = None) -> HandlerContext:
    player = types.SimpleNamespace(
        name="FixturePlayer",
        game=game,
        id=1,
        settings=settings if settings is not None else {"Goal": "default", "Completion Goal": "Friend"},
        slot_data={},
        stats=types.SimpleNamespace(stats={}),
        spoilers={"locations": []},
    )
    return HandlerContext(
        game=game,
        player=player,
        slot=1,
        player_name=player.name,
        seed_name="FixtureSeed",
        timestamp=0.0,
        item_index=7,
        settings=player.settings,
    )


def fixture_send(ctx: HandlerContext, location_name: str = "Somewhere", item_name: str = "Thing"):
    location = types.SimpleNamespace(
        name=location_name, game=ctx.game, player=ctx.player
    )
    item = types.SimpleNamespace(
        name=item_name, location=location, game=ctx.game, receiver=ctx.player
    )
    return SendEvent(
        ctx=ctx,
        sender="Sender",
        receiver="Receiver",
        item=item,
        location=location,
        classification="filler",
        is_filler=True,
    )


# ---------------------------------------------------------------------------
# 1. Registry + conformance
# ---------------------------------------------------------------------------


def check_registry() -> list[str]:
    problems: list[str] = []
    inventory = describe()
    print(f"Registry protocol v{PROTOCOL_VERSION} — {len(inventory)} handler registration(s)")
    for row in inventory:
        declared = row["implements"]
        print(
            f"  {row['game']:32} {row['handler']:20} "
            f"protocol={row['protocol'] if row['protocol'] is not None else 'legacy':6} "
            f"hooks={len(declared) if declared else len(HOOKS)}"
            + (f" {declared}" if declared else "")
        )
        if row["protocol"] not in (None, PROTOCOL_VERSION):
            problems.append(
                f"{row['handler']} declares protocol {row['protocol']}, registry is {PROTOCOL_VERSION}"
            )
    for row in inventory:
        for hook in row["implements"] or []:
            if hook not in HOOKS:
                problems.append(f"{row['handler']} declares unknown hook {hook!r}")
    if not inventory:
        problems.append("no handlers registered at all — auto-discovery likely broken")
    return problems


def check_hooks() -> tuple[list[str], list[str]]:
    """Every declared hook must exist, be callable, and tolerate a fixture without raising.

    v1 hooks are called by the item log with live Game/Player objects, so a fixture failure there is
    a warning (my fixture is not a real room) — the containment path is what covers it in production.
    v2 hooks are called with a HandlerContext the core builds, so a fixture failure is a real bug.
    """
    problems: list[str] = []
    warnings: list[str] = []
    print("\nHook conformance (fixture calls):")
    for row in describe():
        handler = get_handler_fuzzy(row["game"])
        if handler is None:
            continue
        ctx = fixture_ctx(row["game"])
        send = fixture_send(ctx)
        args_for = {
            "handle_item_tracking": (object(), ctx.player, send.item),
            "handle_location_tracking": (object(), ctx.player, send.item),
            "handle_location_hinting": (ctx.player, send.location),
            "handle_state_tracking": (ctx.player, object()),
            "is_location_checkable": (send.location,),
            "should_skip_hint": (send.item,),
            "resolve_conditional_progression": (ctx, send.item),
            "should_post_item_send": (ctx, send),
            "on_item_send": (ctx, send),
        }
        declared = row["implements"]  # None => legacy: only the hooks it actually defines
        if declared is None:
            hooks = sorted(h for h in HOOKS if callable(getattr(handler, h, None)))
        else:
            hooks = sorted(declared)
            for hook in declared:
                if not callable(getattr(handler, hook, None)):
                    problems.append(f"{row['handler']} declares {hook} but does not define it")
        for hook in hooks:
            fn = getattr(handler, hook, None)
            if fn is None:
                continue
            v2 = hook in V2_HOOKS
            try:
                result = fn(*args_for[hook])
            except Exception as exc:
                msg = f"{row['handler']}.{hook} raised on a fixture send: {exc!r}"
                (problems if v2 else warnings).append(msg)
                print(f"  {'FAIL' if v2 else 'warn'} {row['handler']}.{hook}: {exc!r}")
                continue
            expected = {
                "resolve_conditional_progression": (type(None), ClassificationProposal),
                "should_post_item_send": (type(None), PostDecision),
                "on_item_send": (type(None), list),
            }.get(hook)
            if expected and result is not None and not isinstance(result, expected):
                problems.append(
                    f"{row['handler']}.{hook} returned {type(result).__name__}, expected one of "
                    f"{[t.__name__ for t in expected]}"
                )
                print(f"  FAIL {row['handler']}.{hook}: wrong return type {type(result).__name__}")
                continue
            print(f"  ok   {row['handler']}.{hook} -> {result!r}"[:140])
    return problems, warnings


def check_chain_and_containment() -> list[str]:
    """Ordering, first-claim-wins, and the production guarantee that a raising hook degrades."""
    problems: list[str] = []
    print("\nChain + containment:")

    class Probe:
        game = "Probe Game"
        PROTOCOL_VERSION = PROTOCOL_VERSION
        implements = frozenset({"should_post_item_send"})
        order: list[str] = []

        def should_post_item_send(self, ctx, send):
            Probe.order.append("fallthrough")
            return None

    class Claimer:
        game = "Probe Game"
        PROTOCOL_VERSION = PROTOCOL_VERSION
        implements = frozenset({"should_post_item_send"})

        def should_post_item_send(self, ctx, send):
            Probe.order.append("claimed")
            return PostDecision(post=True, reason="probe")

    class Exploder:
        game = "Exploder Game"
        PROTOCOL_VERSION = PROTOCOL_VERSION
        implements = frozenset({"should_post_item_send"})

        def should_post_item_send(self, ctx, send):
            raise RuntimeError("boom")

    register_handler(Probe())
    register_handler(Claimer())
    register_handler(Exploder())

    ctx = fixture_ctx("Probe Game")
    send = fixture_send(ctx)
    result = dispatch("Probe Game", "should_post_item_send", ctx, send)
    if Probe.order != ["fallthrough", "claimed"]:
        problems.append(f"chain order wrong: {Probe.order}")
    if not isinstance(result, PostDecision) or not result.post:
        problems.append(f"first non-None claim not honoured: {result!r}")
    print(f"  ok   chain order {Probe.order}, first claim honoured")

    ectx = fixture_ctx("Exploder Game")
    try:
        fallback = dispatch("Exploder Game", "should_post_item_send", ectx, fixture_send(ectx))
    except Exception as exc:
        problems.append(f"containment failed: exception escaped dispatch ({exc!r})")
        fallback = None
    else:
        if fallback is not None:
            problems.append(f"raising handler changed the outcome: {fallback!r}")
        print("  ok   raising handler contained, default returned")

    if get_handler_fuzzy("GZDoom (Doom 64)") is not get_handler_fuzzy("GZDoom"):
        print("  note no GZDoom handler registered — prefix fallback untested")

    from cmds.ap_scripts.game_handlers import provides

    if not provides("Probe Game", "should_post_item_send"):
        problems.append("provides() said no to a hook a registered handler implements")
    if provides("Probe Game", "on_item_send"):
        problems.append("provides() said yes to a hook no registered handler implements")
    if provides("No Such Game At All", "should_post_item_send"):
        problems.append("provides() said yes for an unregistered game")
    print("  ok   provides() reflects what handlers actually declare")

    if get_handler_fuzzy("Manual_NewSuperMarioBrosDS_zuils") is not None:
        print("  ok   Manual_* prefix fallback resolves")
    return problems


# ---------------------------------------------------------------------------
# 2. Corpus: is every posted filler/currency line explained?
# ---------------------------------------------------------------------------


def chain_for_all():
    seen = []
    for game in ("Spyro 3", "Simon Tatham's Portable Puzzle Collection", "Here Comes Niko!"):
        seen.extend(get_handlers(game))
    return seen


UTILS_SOURCE = REPO_ROOT / "cmds" / "ap_scripts" / "utils.py"


def _load_hcn_friends() -> set:
    """Read `hcn_friends_locations` out of utils.py without importing it.

    utils.py imports discord/psycopg2, which are not installed everywhere this check runs, and the
    list is the authoritative one — so parse the literal instead of duplicating it.
    """
    try:
        text = UTILS_SOURCE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    m = re.search(r"hcn_friends_locations:\s*list\[str\]\s*=\s*\[(.*?)\n\]", text, re.S)
    return set(re.findall(r'"([^"]+)"', m.group(1))) if m else set()


HCN_FRIENDS = _load_hcn_friends()
_CORPUS_SUFFIX = re.compile(r"\s*\(\*\d+/\d+\*\)\s*$")


def _is_hcn_friend(loc: str, item: str) -> bool:
    return _CORPUS_SUFFIX.sub("", loc) in HCN_FRIENDS


INLINE_RULES = (
    (
        "ap_itemlog.py: Simon Tatham's Portable Puzzle Collection — every location posts",
        lambda loc, item: loc.startswith("Puzzle "),
    ),
    (
        f"ap_itemlog.py: Here Comes Niko! friends list under the Friend goal "
        f"({len(HCN_FRIENDS)} locations; a log line cannot confirm the goal)",
        _is_hcn_friend,
    ),
)


def check_corpus(path: pathlib.Path) -> list[str]:
    problems: list[str] = []
    total_lines = 0
    posted_suppressed: list[tuple[str, str, str]] = []
    for raw in path.open(encoding="utf-8", errors="replace"):
        try:
            row = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for line in (row.get("text") or "").split("\n"):
            total_lines += 1
            m = POSTED_LINE.match(line.strip())
            if not m:
                continue
            icon = m.group("icon") or ""
            if icon in SUPPRESSED_ICONS:
                posted_suppressed.append((m.group("loc"), m.group("item"), icon))

    print(
        f"\nCorpus {path.name}: {total_lines} posted sub-lines, "
        f"{len(posted_suppressed)} of them filler/currency (i.e. posted despite the default rule)"
    )

    by_handler: list[tuple[str, str]] = []
    by_inline: dict[str, list[tuple[str, str]]] = {}
    unknown: list[tuple[str, str]] = []
    handlers = chain_for_all()
    for loc, item, icon in posted_suppressed:
        ctx = fixture_ctx(loc.split(":")[0] if ":" in loc else "Corpus")
        send = fixture_send(ctx, location_name=loc, item_name=item)
        if any(
            isinstance(getattr(h, "should_post_item_send", lambda *a: None)(ctx, send), PostDecision)
            for h in handlers
        ):
            by_handler.append((loc, item))
            continue
        for name, rule in INLINE_RULES:
            if rule(loc, item):
                by_inline.setdefault(name, []).append((loc, item))
                break
        else:
            unknown.append((loc, item))

    print(f"  explained by a migrated handler : {len(by_handler)}")
    for name, hits in by_inline.items():
        print(f"  explained by an inline rule     : {len(hits)}  ← {name}")
    print(f"  explained by nothing            : {len(unknown)}")
    for loc, item in unknown[:10]:
        print(f"    - {item!r} from {loc!r}")
    if unknown:
        print(
            "  ^ investigate these: either a rule that predates the archive, or a behaviour change. "
            "A migrated rule must not lose any line it claims."
        )
        problems.append(f"{len(unknown)} posted filler/currency line(s) match no known rule")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", help="archived #item-log .jsonl to check against")
    a = ap.parse_args()

    problems = check_registry()
    hook_problems, hook_warnings = check_hooks()
    problems += hook_problems
    problems += check_chain_and_containment()
    if a.corpus:
        problems += check_corpus(pathlib.Path(a.corpus))

    print("\n" + ("FAILED" if problems else "OK"))
    for w in hook_warnings:
        print(f"  ~ warning: {w}")
    for p in problems:
        print(f"  ! {p}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
