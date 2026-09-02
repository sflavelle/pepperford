"""
Game handler registry for game-specific tracking logic.

Each module in this directory that exposes a module-level `handler`
attribute (an object with a `.game` attribute and any of the
handle_* methods) is automatically registered on import.

Handlers are keyed by exact game name string. To handle multiple games
from one file, set `handler.game` to a tuple of strings.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any, Dict, List, Optional, Protocol, Tuple

logger = logging.getLogger("ap_itemlog.game_handlers")

# ---------------------------------------------------------------------------
# Protocol (duck typing – no strict inheritance required)
# ---------------------------------------------------------------------------

class GameHandler(Protocol):
    """Interface each handler module is expected to satisfy.

    Every method is optional — return None / pass to fall through to
    the default logic in utils.py.
    """

    game: str | Tuple[str, ...]

    def handle_item_tracking(
        self, game: Any, player: Any, item: Any
    ) -> Optional[str]:
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

    def should_skip_hint(self, item: Any) -> Optional[bool]:
        """Return True/False to override, or None for default."""
        ...

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_handlers: Dict[str, "GameHandler"] = {}


def register_handler(handler: "GameHandler") -> None:
    """Register a handler instance for its game(s)."""
    games = handler.game if isinstance(handler.game, tuple) else (handler.game,)
    for g in games:
        existing = _handlers.get(g)
        if existing is not None:
            logger.warning(
                f"Handler for game {g!r} already registered "
                f"(existing={type(existing).__module__}), overwriting"
            )
        _handlers[g] = handler


def get_handler(game_name: str) -> Optional["GameHandler"]:
    """Look up a handler by exact game name."""
    return _handlers.get(game_name)


def get_handler_fuzzy(game_name: str) -> Optional["GameHandler"]:
    """Look up a handler by exact game name, then by prefix for GZDoom variants."""
    # Exact match first
    h = _handlers.get(game_name)
    if h is not None:
        return h
    # GZDoom variants: "GZDoom (Doom 64)" etc. — try "GZDoom" prefix
    if game_name.startswith("GZDoom"):
        h = _handlers.get("GZDoom")
        if h is not None:
            return h
    # Manual games: "Manual_NewSuperMarioBrosDS_zuils" etc.
    if game_name.startswith("Manual_"):
        h = _handlers.get("Manual")
        if h is not None:
            return h
    return None


# ---------------------------------------------------------------------------
# Auto-discovery
# ---------------------------------------------------------------------------

def _discover() -> None:
    """Walk all modules in this package and auto-register those with a handler."""
    pkg_path = __path__[0] if __path__ else __path__._path[0]  # type: ignore[attr-defined]
    for finder, modname, ispkg in pkgutil.iter_modules([pkg_path]):
        if ispkg or modname.startswith("_"):
            continue
        try:
            mod = importlib.import_module(f".{modname}", __package__)
        except Exception as exc:
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
        register_handler(handler)
        logger.info(
            f"Registered game handler: {modname} → {handler.game}"
        )


_discover()