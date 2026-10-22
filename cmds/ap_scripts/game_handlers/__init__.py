"""
Game handler registry — all game-specific itemlog logic lives here, not in ap_itemlog.py.

Each module in this directory that exposes a module-level `handler` attribute is auto-registered on
import. A handler declares the game(s) it covers and the hooks it implements:

    class MyHandler:
        game = "Some Game"                 # or a tuple of game names
        PROTOCOL_VERSION = PROTOCOL_VERSION
        implements = frozenset({"should_post_item_send", "on_item_send"})

        def should_post_item_send(self, ctx, send) -> PostDecision | None: ...

    handler = MyHandler()

Protocol v2 (2026-10-03) adds `HandlerContext`, the three hooks at the bottom of `GameHandler`,
chained dispatch and error containment. Everything from v1 keeps working unchanged: hooks are still
duck-typed and optional, and a handler declaring neither `PROTOCOL_VERSION` nor `implements` is
treated as a legacy handler (candidate for every hook, no context object).

Why a context object: the v1 hooks each took a different slice of the world — `(game, player, item)`,
`(player, game)`, `(item,)` — so anything new had to be threaded through every call site, and adding
a parameter to an existing hook silently breaks every handler implementing it. A frozen
`HandlerContext` is extended by adding a field; no existing handler changes.

Hook contract: return a value to claim the decision, or `None` to fall through to the next handler
and finally to the default logic in ap_itemlog.py / utils.py. Handlers never write to the database:
for classifications they return a `ClassificationProposal` and the core applies it under the usual
policy (fill blanks or raise; manual edits win; downgrades held for review).
"""

from __future__ import annotations

import builtins
import importlib
import logging
import os
import pkgutil
from collections.abc import Iterator, Mapping, MutableMapping
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, Tuple

logger = logging.getLogger("ap_itemlog.game_handlers")

PROTOCOL_VERSION = 2

# Re-raise handler exceptions instead of falling back to the default. Off in production: one broken
# handler must not take the item log down mid-seed. Turn it on in tests (`_selfcheck.py`, or
# AP_HANDLERS_STRICT=1).
STRICT = os.environ.get("AP_HANDLERS_STRICT", "").strip().lower() in {"1", "true", "yes", "on"}

HOOKS: Tuple[str, ...] = (
    # --- protocol v1 -------------------------------------------------------------------------
    "handle_item_tracking",
    "handle_location_tracking",
    "handle_location_hinting",
    "handle_state_tracking",
    "is_location_checkable",
    "should_skip_hint",
    # --- protocol v2 -------------------------------------------------------------------------
    "resolve_classification",
    "resolve_conditional_progression",  # deprecated alias of resolve_classification
    "should_post_item_send",
    "on_item_send",
    "on_slot_loaded",
)


# ---------------------------------------------------------------------------
# Values hooks exchange
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HandlerContext:
    """Everything a hook may need about the event being processed.

    Frozen on purpose: a hook can read the world here but cannot rewrite identity mid-dispatch.
    Mutable state goes through `ctx.state(ns)` (persisted) or `ctx.player` (the live object).
    """

    game: str
    player: Any = None  # the acting Player; for a send, the slot that owns the location
    itemlog: Any = None  # the running logger/Game object, for escape hatches
    slot: Optional[int] = None
    player_name: Optional[str] = None
    seed_name: Optional[str] = None
    room_id: Optional[str] = None
    datapackage_checksum: Optional[str] = None
    timestamp: Optional[float] = None
    item_index: Optional[int] = None  # absolute index in the room's log buffer; None when unknown
    settings: Mapping[str, Any] = field(default_factory=dict)

    def dedupe_key(self, *extra: Any) -> str:
        """Stable key for 'act once per thing' — survives a replayed prefix after a crash."""
        parts = [
            str(self.seed_name or "?"),
            str(self.slot if self.slot is not None else "?"),
            str(self.item_index if self.item_index is not None else "?"),
            *(str(e) for e in extra),
        ]
        return ":".join(parts)

    def setting(self, name: str, default: Any = None) -> Any:
        """Read one of the player's settings (display-name keys, as the room reports them)."""
        try:
            return self.settings[name]
        except (KeyError, TypeError):
            return default

    def state(self, namespace: str) -> "NamespacedState":
        """A persisted dict for one handler, surviving restarts via Player.stats serialisation."""
        store = getattr(getattr(self.player, "stats", None), "stats", None)
        if not isinstance(store, dict):  # detached / fixture context
            store = _FIXTURE_STATE.setdefault(id(self), {})
        return NamespacedState(store, namespace)


_FIXTURE_STATE: Dict[int, Dict[str, Any]] = {}


class NamespacedState(MutableMapping):
    """Dict-like view into a player's persisted stats, scoped to one handler namespace."""

    __slots__ = ("_store", "_key")

    def __init__(self, store: Dict[str, Any], namespace: str) -> None:
        self._store = store
        self._key = f"handler_state:{namespace}"
        if not isinstance(store.get(self._key), dict):
            store[self._key] = {}

    def __getitem__(self, k: str) -> Any:
        return self._store[self._key][k]

    def __setitem__(self, k: str, v: Any) -> None:
        self._store[self._key][k] = v

    def __delitem__(self, k: str) -> None:
        del self._store[self._key][k]

    def __iter__(self) -> Iterator[str]:
        return iter(self._store[self._key])

    def __len__(self) -> int:
        return len(self._store[self._key])


@dataclass(frozen=True)
class SendEvent:
    """One 'X sent Y to Z (location)' line, at the moment the send log decides what to do with it."""

    ctx: HandlerContext
    sender: str = ""
    receiver: str = ""
    item: Any = None
    location: Any = None
    classification: Optional[str] = None
    is_filler: bool = False
    is_currency: bool = False
    skip_msg: bool = False
    in_release: bool = False  # a release for this sender is already buffered
    in_collect: bool = False  # a collect for this receiver is already buffered

    @property
    def location_name(self) -> str:
        return str(getattr(self.location, "name", "") or "")

    @property
    def item_name(self) -> str:
        return str(getattr(self.item, "name", "") or "")


@dataclass(frozen=True)
class PostDecision:
    """Returned by `should_post_item_send`.

    `post=True` posts even though the default rule would suppress it (filler/currency); `post=False`
    suppresses. The core still applies release/collect batching either way — claiming a post does not
    mean 'ignore the buffers'. `reason` is kept for the log line and for tests.
    """

    post: bool
    reason: Optional[str] = None
    icon: Optional[str] = None
    dedupe_key: Optional[str] = None


@dataclass(frozen=True)
class ClassificationProposal:
    """Returned by `resolve_conditional_progression` (and by any future classification hook).

    The handler proposes, the core decides. `downgrade=True` marks a proposal that would lower an
    existing stored value — those are held for review rather than applied, matching the policy in
    classify_from_multidata.py.
    """

    classification: str
    reason: str
    source: str = "handler"
    confidence: str = "high"  # 'high' | 'review'
    downgrade: bool = False


@dataclass(frozen=True)
class Announcement:
    """Returned by `on_item_send` — an area unlock, a level completion, a milestone.

    Handlers return text; the core renders and posts it **in the log channel** — announcements are
    not routed through the chat handler (splatsune, 2026-10-03), so formatting, goaled-player dimming
    and rate limiting all stay in the core. `dedupe_key` must be stable for the same underlying
    event: the logger resumes from a stored line number, so a crash mid-seed replays a prefix.
    """

    kind: str  # 'area_access' | 'level_complete' | 'milestone' | 'custom'
    text: str
    dedupe_key: Optional[str] = None
    state: Optional[Mapping[str, Any]] = None  # state to persist if the announcement is posted


# ---------------------------------------------------------------------------
# Protocol (duck typing – no strict inheritance required)
# ---------------------------------------------------------------------------


class GameHandler(Protocol):
    """Interface each handler module is expected to satisfy.

    Every method is optional — return None / pass to fall through to the default logic in
    ap_itemlog.py and utils.py.
    """

    game: str | Tuple[str, ...]

    def handle_item_tracking(self, game: Any, player: Any, item: Any) -> Optional[str]:
        """Return a replacement item name string, or None to use default."""
        ...

    def handle_location_tracking(
        self, game: Any, player: Any, item: Any, use_everywhere: bool = False
    ) -> Optional[str]:
        """Return a replacement location name string, or None to use default."""
        ...

    def handle_location_hinting(
        self, player: Any, location: Any
    ) -> Optional[Tuple[List[str], str]]:
        """Return (requirements, extra_info) or None to use default."""
        ...

    def handle_state_tracking(self, player: Any, game: Any) -> None:
        """Mutate player.stats / set player.stats.goal_str as side-effect."""
        ...

    def is_location_checkable(self, location: Any) -> Optional[bool]:
        """Return True/False to override, or None for default."""
        ...

    def should_skip_hint(self, ctx: HandlerContext, item: Any) -> Optional[bool]:
        """Return True to suppress this hint, or None for the default.

        Call site: the hint branch in ap_itemlog.py, where the Balatro shop-item exclusion sits —
        a hint fires the moment an item appears in a shop or pack, usually just before the player
        buys it, so a game can decide some hints are noise.
        """
        ...

    # --- protocol v2 -------------------------------------------------------------------------

    def resolve_classification(
        self, ctx: HandlerContext, item: Any, stored: Optional[str] = None
    ) -> Optional[ClassificationProposal]:
        """Resolve a class the database cannot answer for this seed.

        Called when the stored value is `None` (no row / unclassified) or `"conditional
        progression"` (progression only under some options). Return a ClassificationProposal when
        the world's own rule is known, else None to leave it alone. Read conditions from
        `ctx.settings` and fall back to the world's default, never to a guess.
        """
        ...

    def resolve_conditional_progression(
        self, ctx: HandlerContext, item: Any, default: str = "conditional progression"
    ) -> Optional[ClassificationProposal]:
        """Deprecated alias, kept so handlers written before v2.1 keep working.

        Implement `resolve_classification` instead: it is called for unclassified rows too, which
        is where the simple-world rules ("everything here is progression") belong.
        """
        ...

    def should_post_item_send(
        self, ctx: HandlerContext, send: SendEvent
    ) -> Optional[PostDecision]:
        """Override the 'filler is not posted' rule for this one send, or None for the default."""
        ...

    def on_item_send(self, ctx: HandlerContext, send: SendEvent) -> Optional[List[Announcement]]:
        """Return announcements (area access, level completion) triggered by this send, or None."""
        ...

    def on_slot_loaded(self, ctx: HandlerContext) -> None:
        """One call per slot, after the spoiler and settings are loaded.

        This is where per-seed setup belongs when it is not a per-send decision: reading
        slot_data to compute a target (Trackmania), deriving a requirement from spoiler locations
        (SMW's hidden eggs), building a name table (gzDoom maps), or priming a layout
        (SBURBelago). Return value is ignored; do the work as a side effect and keep any result in
        `ctx.state(<handler>)`.
        """
        ...


# ---------------------------------------------------------------------------
# Registry (a chain per game: base → game → overlay; first claim wins)
# ---------------------------------------------------------------------------

_handlers: Dict[str, List[Any]] = {}
_failed: List[str] = []


def _games_of(handler: Any) -> Tuple[str, ...]:
    games = getattr(handler, "game", None)
    if games is None:
        return ()
    if isinstance(games, (tuple, list, set)):
        return tuple(games)
    return (games,)


def implements_of(handler: Any) -> frozenset:
    """The hooks a handler claims. No declaration = legacy, so every hook is a candidate."""
    declared = getattr(handler, "implements", None)
    if declared is None:
        return frozenset(HOOKS)
    if isinstance(declared, str):
        declared = (declared,)
    return frozenset(declared)


def _check_protocol(handler: Any, modname: str) -> bool:
    declared = getattr(handler, "PROTOCOL_VERSION", None)
    if declared is None:
        logger.info(
            "Handler %s declares no PROTOCOL_VERSION — treating as legacy (candidate for every "
            "hook, no context object); consider declaring implements=...",
            modname,
        )
        return True
    if declared != PROTOCOL_VERSION:
        logger.error(
            "Handler %s declares PROTOCOL_VERSION=%s but this registry is v%s — hooks may be called "
            "with an unexpected signature",
            modname,
            declared,
            PROTOCOL_VERSION,
        )
        return not STRICT
    return True


def register_handler(handler: Any, priority: int = 0) -> None:
    """Register a handler for its game(s). Handlers for one game form a chain, first claim wins."""
    games = _games_of(handler)
    if not games:
        logger.warning("Handler %r has no .game — not registered", type(handler).__module__)
        return
    for g in games:
        chain = _handlers.setdefault(g, [])
        if builtins.any(h is handler for h in chain):
            continue
        if priority:
            chain.insert(0, handler)
        else:
            chain.append(handler)
        if len(chain) > 1:
            logger.info(
                "Game %r now has a handler chain: %s",
                g,
                " -> ".join(type(h).__module__.rsplit(".", 1)[-1] for h in chain),
            )


def get_handlers(game_name: str) -> List[Any]:
    """The full chain for an exact game name (never None; empty list when unhandled)."""
    return list(_handlers.get(game_name, ()))


def get_handler(game_name: Optional[str]) -> Optional[Any]:
    """Back-compat: the first handler in the chain, or None."""
    chain = _handlers.get(game_name) if game_name else None
    return chain[0] if chain else None


def get_handler_fuzzy(game_name: Optional[str]) -> Optional[Any]:
    """Back-compat: first handler for an exact name, then the GZDoom/Manual prefix chains."""
    chain = _resolve_chain(game_name)
    return chain[0] if chain else None


def _resolve_chain(game_name: Optional[str]) -> List[Any]:
    if not game_name:
        return []
    chain = _handlers.get(game_name)
    if chain:
        return list(chain)
    # GZDoom variants: "GZDoom (Doom 64)" etc. — fall back to the "GZDoom" chain
    if game_name.startswith("GZDoom"):
        return list(_handlers.get("GZDoom", ()))
    # Manual games: "Manual_NewSuperMarioBrosDS_zuils" etc.
    if game_name.startswith("Manual_"):
        return list(_handlers.get("Manual", ()))
    return []


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def dispatch(
    game_name: Optional[str],
    hook: str,
    *args: Any,
    default: Any = None,
    fuzzy: bool = True,
) -> Any:
    """Walk the chain for `hook`, returning the first non-None result.

    A handler that raises is logged and skipped (production) or re-raised (AP_HANDLERS_STRICT=1), so
    a broken handler degrades to the default rule instead of losing the week's log.
    """
    chain = _resolve_chain(game_name) if fuzzy else get_handlers(game_name or "")
    for h in chain:
        if hook not in implements_of(h):
            continue
        fn = getattr(h, hook, None)
        if fn is None:
            continue
        try:
            result = fn(*args)
        except Exception:
            if STRICT:
                raise
            logger.exception(
                "Handler %s failed in %s for game %r — falling back to default",
                type(h).__module__.rsplit(".", 1)[-1],
                hook,
                game_name,
            )
            continue
        if result is not None:
            return result
    return default


def provides(game_name: Optional[str], hook: str) -> bool:
    """Does any handler in the chain implement `hook`?

    Needed for side-effecting hooks that return None (`handle_state_tracking`): `dispatch` cannot
    distinguish "did nothing" from "did it and returned None", so the caller has to decide whether
    the handler owns the behaviour *before* calling it.
    """
    return any(
        hook in implements_of(h) and callable(getattr(h, hook, None))
        for h in _resolve_chain(game_name)
    )


def dispatch_announcements(
    game_name: Optional[str], ctx: HandlerContext, send: Any
) -> List["Announcement"]:
    """Collect announcements from every handler in the chain, dropping ones already posted.

    Dedupe state lives in each handler's own namespace (`ctx.state(<module>)`), keyed by the
    announcement's `dedupe_key`, so a replayed prefix after a crash does not re-announce. Any
    `Announcement.state` is persisted when the announcement is accepted. The caller renders the
    returned text — announcements go to the log channel, never through the chat handler.
    """
    posted: List["Announcement"] = []
    for h in _resolve_chain(game_name):
        if "on_item_send" not in implements_of(h):
            continue
        fn = getattr(h, "on_item_send", None)
        if fn is None:
            continue
        try:
            result = fn(ctx, send)
        except Exception:
            if STRICT:
                raise
            logger.exception(
                "Handler %s failed in on_item_send for game %r — skipping its announcements",
                type(h).__module__.rsplit(".", 1)[-1],
                game_name,
            )
            continue
        if not result:
            continue
        ns = type(h).__module__.rsplit(".", 1)[-1]
        state = ctx.state(ns)
        announced = state.get("announced")
        if not isinstance(announced, dict):
            announced = {}
        for a in result if isinstance(result, list) else [result]:
            key = a.dedupe_key or f"{ns}:{a.kind}:{a.text}"
            if announced.get(key):
                continue
            announced[key] = str(ctx.item_index if ctx.item_index is not None else ctx.timestamp)
            if a.state:
                state.update(a.state)
            state["announced"] = announced
            posted.append(a)
    return posted


def dispatch_all(game_name: Optional[str], hook: str, *args: Any) -> List[Any]:
    """Call `hook` on every handler in the chain and collect the non-None results (broadcast)."""
    results: List[Any] = []
    for h in _resolve_chain(game_name):
        if hook not in implements_of(h):
            continue
        fn = getattr(h, hook, None)
        if fn is None:
            continue
        try:
            r = fn(*args)
        except Exception:
            if STRICT:
                raise
            logger.exception("Handler %s failed in %s", type(h).__module__, hook)
            continue
        if r is not None:
            results.extend(r if isinstance(r, list) else [r])
    return results


def describe() -> List[Dict[str, Any]]:
    """Registry inventory, for the self-check and for a startup log line."""
    out = []
    for game, chain in sorted(_handlers.items()):
        for h in chain:
            out.append(
                {
                    "game": game,
                    "handler": type(h).__module__.rsplit(".", 1)[-1],
                    "protocol": getattr(h, "PROTOCOL_VERSION", None),
                    "implements": sorted(implements_of(h))
                    if getattr(h, "implements", None)
                    else None,
                }
            )
    return out


# ---------------------------------------------------------------------------
# Auto-discovery
# ---------------------------------------------------------------------------


def summary() -> str:
    """One-line registry health report, logged at startup.

    A handler module that fails to import silently loses that game's entire rule set, so the boot
    log says what loaded, what did not, and which games are covered.
    """
    games = sorted(_handlers)
    loaded = ", ".join(
        f"{g} ({type(h).__module__.rsplit('.', 1)[-1]})" for g in games for h in _handlers[g]
    )
    text = f"game handlers v{PROTOCOL_VERSION}: {len(games)} game(s) covered"
    if loaded:
        text += f" — {loaded}"
    if _failed:
        text += f" — FAILED TO IMPORT: {', '.join(_failed)}"
    return text


def _discover() -> None:
    """Walk all modules in this package and auto-register those with a handler.

    Modules whose name starts with `_` are skipped on purpose (the self-check lives here), as are
    subpackages.
    """
    pkg_path = __path__[0] if __path__ else __path__._path[0]  # type: ignore[attr-defined]
    for finder, modname, ispkg in pkgutil.iter_modules([pkg_path]):
        if ispkg or modname.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f".{modname}", __package__)
        except Exception as exc:
            _failed.append(modname)
            logger.error(f"Failed to import handler module {modname!r}: {exc}")
            continue
        handler = getattr(mod, "handler", None)
        if handler is None:
            logger.debug(f"Module {modname!r} has no `handler` attribute, skipping")
            continue
        if not hasattr(handler, "game"):
            logger.warning(
                f"Module {modname!r} has `handler` but it lacks `.game`, skipping"
            )
            continue
        if not _check_protocol(handler, modname):
            continue
        register_handler(handler)
        logger.info(
            f"Registered game handler: {modname} → {handler.game}"
        )


_discover()
logger.info(summary())
