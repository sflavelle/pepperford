"""Developer tooling for moving inline game code into game handlers.

Not imported by the bot. Run as a module:

    python -m cmds.ap_scripts.game_handlers._migrate list <hook>
    python -m cmds.ap_scripts.game_handlers._migrate show <hook> <game>
    python -m cmds.ap_scripts.game_handlers._migrate check <hook> <game>
    python -m cmds.ap_scripts.game_handlers._migrate migrate <hook> <game>
    python -m cmds.ap_scripts.game_handlers._migrate migrate-all <hook>

Why a baseline commit: the old arms are read out of git at BASE_COMMIT, which is the tree before this
migration started. That means an arm can be deleted from the working file and still be available for
the equivalence check, and re-running the tool never reads a half-migrated file.

The check runs the old arm and the new handler over a matrix of settings/counts derived from the arm
itself (every `settings["X"]`, `slot_data["X"]` and `get_item_count("Y")` it mentions), then compares
what the two produce. A handler that differs anywhere is reported with the combination that broke it.
"""

from __future__ import annotations

import ast
import copy
import itertools
import pathlib
import re
import subprocess
import sys
import types
from typing import Any, Dict, List, Optional, Sequence, Tuple

BASE_COMMIT = "b35cb3d"
REPO = pathlib.Path(__file__).resolve().parents[3]
UTILS_PATH = "cmds/ap_scripts/utils.py"

# hook -> (function (or method) name, handler parameters, how its output is compared)
HOOKS: Dict[str, Tuple[str, List[str], str]] = {
    "handle_item_tracking": ("handle_item_tracking", ["itemlog", "player", "item"], "return"),
    "handle_location_tracking": (
        "handle_location_tracking",
        ["itemlog", "player", "item", "use_everywhere"],
        "return",
    ),
    "handle_state_tracking": ("handle_state_tracking", ["player", "game"], "stats"),
    "handle_location_hinting": ("handle_location_hinting", ["player", "location"], "hinting"),
    "is_location_checkable": ("fetch_islocation_checkable", ["location"], "return"),
}

# Names that are builtins or harmless module attributes: an arm referring to them needs no help.
BUILTINS = {
    "abs", "all", "any", "bool", "dict", "enumerate", "float", "int", "len", "list", "max", "min",
    "next", "range", "reversed", "round", "set", "sorted", "str", "sum", "tuple", "zip", "Exception",
    "IndexError", "KeyError", "ValueError", "TypeError", "AttributeError", "NameError", "RuntimeError",
    "StopIteration", "ZeroDivisionError",
}

# Locals the enclosing function sets up before the arm runs. The handler recreates them.
HOOK_PRELUDE: Dict[str, str] = {
    "handle_item_tracking": (
        "item = item.name\n"
        "settings = player.settings\n"
        "slot_data = player.slot_data\n"
        "spoiler = itemlog.spoiler_log[str(player)]\n"
        "count = player.get_item_count(item)\n"
        "game = player.game\n"
        "game_name = player.game"
    ),
    "handle_location_tracking": (
        "location = item.location.name\n"
        "settings = player.settings\n"
        "slot_data = player.slot_data\n"
        "spoiler = itemlog.spoiler_log[str(player)]\n"
        "game_name = player.game"
    ),
    "handle_state_tracking": (
        "settings = player.settings\n"
        "slot_data = player.slot_data\n"
        "spoiler = game.spoiler_log[str(player)]\n"
        "player_game = player.game\n"
        "goal = \"\"\n"
        "goal_str = \"\""
    ),
    "is_location_checkable": "",
    "handle_location_hinting": "",
}

# The enclosing function does this after the arm; a handler must do it too.
HOOK_POSTLUDE: Dict[str, str] = {
    "handle_state_tracking": "player.stats.goal_str = goal_str",
}

# What an arm may still reference after its prelude. Anything else means a hand migration: the name
# is either shared across arms (dk64_goal is defined in another case) or a utils module global.
HOOK_ALLOWED: Dict[str, set] = {
    "handle_item_tracking": {
        "item", "settings", "slot_data", "spoiler", "count", "game", "game_name", "logger", "player",
        "itemlog", "ItemObject", "gzd", "item_table", "re",
    },
    "handle_location_tracking": {
        "location", "settings", "slot_data", "spoiler", "game_name", "logger", "player", "item",
        "itemlog", "math", "re",
    },
    "handle_state_tracking": {
        "settings", "slot_data", "spoiler", "player_game", "goal", "goal_str", "logger", "player",
        "game",
    },
    "is_location_checkable": {"logger"},
    "handle_location_hinting": {"add_requirement", "requirements", "extra_info", "costs", "location",
                                "player", "settings", "slot_data", "l", "logger", "game_name"},
}


# --------------------------------------------------------------------------------------------------
# Source handling
# --------------------------------------------------------------------------------------------------

def old_source() -> str:
    """utils.py as it was before the migration started."""
    out = subprocess.run(
        ["git", "show", f"{BASE_COMMIT}:{UTILS_PATH}"],
        cwd=REPO, capture_output=True, text=True, check=True,
    )
    return out.stdout


def _function_node(tree: ast.AST, name: str) -> Optional[ast.AST]:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _cases_by_game(func: ast.AST) -> Dict[str, ast.match_case]:
    """Every `case "Game":` of the function's OWN match statements.

    A match nested inside an arm switches on that game's options (goal, ending, chapter), not on games,
    so its cases are excluded: treating them as game names once deleted `case "Crows":` out of the
    middle of another arm.
    """
    parent = {}
    for node in ast.walk(func):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    def enclosed(node: ast.AST) -> bool:
        p = parent.get(node)
        while p is not None and p is not func:
            if isinstance(p, ast.Match):
                return True
            p = parent.get(p)
        return False

    found: Dict[str, ast.match_case] = {}
    for node in ast.walk(func):
        if not isinstance(node, ast.Match) or enclosed(node):
            continue
        for case in node.cases:
            pattern = case.pattern
            values = []
            if isinstance(pattern, ast.MatchValue) and isinstance(pattern.value, ast.Constant):
                values = [pattern.value.value]
            elif isinstance(pattern, ast.MatchOr):
                values = [
                    p.value.value
                    for p in pattern.patterns
                    if isinstance(p, ast.MatchValue) and isinstance(p.value, ast.Constant)
                ]
            for value in values:
                if isinstance(value, str) and value != "_":
                    found.setdefault(value, case)
    return found


def arm_source(hook: str, game: str, source: Optional[str] = None) -> str:
    func_name = HOOKS[hook][0]
    tree = ast.parse(source if source is not None else old_source())
    func = _function_node(tree, func_name)
    if func is None:
        raise SystemExit(f"{func_name} not found")
    case = _cases_by_game(func).get(game)
    if case is None:
        raise SystemExit(f"no inline arm for {game!r} in {func_name}")
    return ast.unparse(ast.Module(body=case.body, type_ignores=[]))


def free_names(source: str, params: Sequence[str]) -> List[str]:
    """Names the arm reads but never assigns itself (module globals and params aside)."""
    tree = ast.parse(source)
    loaded, stored = set(), set(params)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            (loaded if isinstance(node.ctx, ast.Load) else stored).add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            stored.add(node.name)
        elif isinstance(node, ast.arg):
            stored.add(node.arg)
    return sorted(n for n in loaded - stored if n not in BUILTINS and n not in {"True", "False", "None"})


def delete_arm(hook: str, game: str) -> bool:
    """Remove the inline `case "<game>":` block from the working copy. Returns whether it changed."""
    path = REPO / UTILS_PATH
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    func = _function_node(tree, HOOKS[hook][0])
    if func is None:
        return False
    case = _cases_by_game(func).get(game)
    if case is None:
        return False
    lines = text.splitlines(keepends=True)
    # span: the `case` header through the last line of its body, inclusive
    start = case.pattern.lineno - 1
    end = max(getattr(stmt, "end_lineno", stmt.lineno) for stmt in case.body)
    del lines[start:end]
    path.write_text("".join(lines), encoding="utf-8")
    return True


# --------------------------------------------------------------------------------------------------
# Handler module generation
# --------------------------------------------------------------------------------------------------

def slug(game: str) -> str:
    name = game.lower()
    name = name.replace("'", "").replace("&", " and ")
    name = re.sub(r"[^a-z0-9]+", "_", name).strip("_")
    return name


def class_name(game: str) -> str:
    parts = re.sub(r"[^A-Za-z0-9 ]+", " ", game).split()
    return "".join(p[:1].upper() + p[1:] for p in parts) + "Handler"


def module_path(game: str) -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent / f"{slug(game)}.py"


PREAMBLE_NAMES = {
    "settings": "player.settings",
    "slot_data": "player.slot_data",
    "game_name": "player.game",
    "player_game": "player.game",
}


def _method_source(hook: str, game: str, body: str, free: Sequence[str]) -> str:
    params = HOOKS[hook][1]
    prelude = HOOK_PRELUDE.get(hook, "")
    postlude = HOOK_POSTLUDE.get(hook, "")

    def block(text: str) -> str:
        return "\n".join("        " + line if line.strip() else "" for line in text.splitlines())

    parts = []
    if prelude:
        parts.append(block(prelude))
    parts.append(block(body))
    if postlude:
        parts.append(block(postlude))
    return (
        f"    def {hook}(self, {', '.join(params)}):\n"
        + "\n".join(parts)
        + "\n"
    )


def generate_module(hook: str, game: str, body: str, free: Sequence[str]) -> str:
    method = _method_source(hook, game, body, free)
    return (
        f'"""{game} — game-specific handling extracted from utils.{HOOKS[hook][0]}.\n\n'
        f"Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check\n"
        f"(`python -m cmds.ap_scripts.game_handlers._migrate check {hook} \"{game}\"`) runs the old arm\n"
        f'against this handler.\n"""\n\n'
        "from __future__ import annotations\n\n"
        "import logging\n\n"
        "from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION\n\n"
        'logger = logging.getLogger("ap_itemlog")\n\n\n'
        f"class {class_name(game)}:\n"
        f'    game = "{game}"\n'
        "    PROTOCOL_VERSION = PROTOCOL_VERSION\n"
        f'    implements = frozenset({{"{hook}"}})\n\n'
        f"{method}\n"
        f"handler = {class_name(game)}()\n"
    )


def append_hook(path: pathlib.Path, game: str, hook: str, body: str, free: Sequence[str]) -> None:
    """Add another hook method to an existing handler module."""
    text = path.read_text(encoding="utf-8")
    method = _method_source(hook, game, body, free)
    text = text.replace(
        f'implements = frozenset({{"{_first_hook(text)}"}})',
        f'implements = frozenset({{"{_first_hook(text)}", "{hook}"}})',
        1,
    )
    head, sep, tail = text.partition("\nhandler = ")
    path.write_text(head.rstrip("\n") + "\n\n\n" + method + "\n" + sep + tail, encoding="utf-8")


def _first_hook(text: str) -> str:
    match = re.search(r'implements = frozenset\(\{"([^"]+)"', text)
    return match.group(1) if match else ""


# --------------------------------------------------------------------------------------------------
# Equivalence harness
# --------------------------------------------------------------------------------------------------

def _collect_keys(body: str) -> Tuple[List[str], List[str], List[str]]:
    """settings keys, slot_data keys and counted item names the arm mentions."""
    tree = ast.parse(body)
    settings: List[str] = []
    slot_data: List[str] = []
    counted: List[str] = []

    def add(target: List[str], value: Any) -> None:
        if isinstance(value, str) and value not in target:
            target.append(value)

    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
            if isinstance(node.value, ast.Name) and node.value.id == "settings":
                add(settings, node.slice.value)
            elif isinstance(node.value, ast.Name) and node.value.id == "slot_data":
                add(slot_data, node.slice.value)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            method = node.func.attr
            args = [a.value for a in node.args if isinstance(a, ast.Constant)]
            if method == "get" and isinstance(node.func.value, ast.Name):
                if node.func.value.id == "settings":
                    add(settings, args[0] if args else None)
                elif node.func.value.id == "slot_data":
                    add(slot_data, args[0] if args else None)
            elif method in {"get_item_count", "has_item"} and args:
                add(counted, args[0])
    return settings, slot_data, counted


def _combinations(settings: List[str], slot_keys: List[str], counted: List[str], limit: int = 64):
    """A deterministic matrix of settings/slot/count states, capped at `limit`."""
    setting_values = [[True, False] for _ in settings]
    slot_values = [[1, 0] for _ in slot_keys]
    count_values = [[0, 1, 2] for _ in counted]
    all_axes = setting_values + slot_values + count_values
    if not all_axes:
        return [({}, {}, {})]
    combos = itertools.product(*all_axes)
    out = []
    for combo in combos:
        if len(out) >= limit:
            break
        s = dict(zip(settings, combo[: len(settings)]))
        sd = dict(zip(slot_keys, combo[len(settings): len(settings) + len(slot_keys)]))
        c = dict(zip(counted, combo[len(settings) + len(slot_keys):]))
        out.append((s, sd, c))
    return out


def _build_env(game: str, settings: Dict[str, Any], slot_data: Dict[str, Any], counts: Dict[str, int]):
    import cmds.ap_scripts.utils as u

    instance = u.Game()
    instance.name = game
    instance.has_spoiler = True
    player = u.Player("MigrateTest", game, 1, instance)
    # Settings start empty: Location.__init__ runs the hinting hook, and the real code only reaches it
    # when the player has settings. Assign them after the location exists.
    player.settings = {}
    player.slot_data = dict(slot_data)
    player.inventory = [
        types.SimpleNamespace(name=name)
        for name, count in counts.items()
        for _ in range(count)
    ]
    item = u.Item("Sender", player, "Test Item", "Test Location")
    location = u.Location(item, player, "Test Location", game)
    player.settings = dict(settings)
    itemlog = types.SimpleNamespace(
        has_spoiler=True,
        spoiler_log={str(player): {}},
        players={player.name: player},
        game=instance,
    )
    return u, player, item, location, itemlog


def _wrap(body: str, prelude: str = "", postlude: str = "", namespace: Optional[Dict[str, Any]] = None) -> str:
    """Indent prelude + arm + postlude into a callable named _arm.

    The prelude names are also passed as default arguments: an arm that assigns one of them in a branch
    would otherwise make it a local of _arm and raise UnboundLocalError on the other branch, which the
    original code never did (the name was already bound in the enclosing function).
    """
    inner = "\n".join(part for part in (prelude, body, postlude) if part and part.strip())
    params = []
    if namespace:
        for name in (
            "player", "item", "itemlog", "settings", "slot_data", "spoiler", "count", "game",
            "game_name", "location", "player_game", "goal", "goal_str", "use_everywhere", "logger",
            "l", "requirements", "costs", "extra_info", "add_requirement", "ItemObject",
        ):
            if name in namespace:
                params.append(f"{name}={name}")
    return "def _arm(" + ", ".join(params) + "):\n" + "\n".join(
        "    " + line if line.strip() else "" for line in inner.splitlines()
    )


def _run_old(hook: str, game: str, body: str, env) -> Any:
    u, player, item, location, itemlog = env

    # The enclosing functions return before any arm when there is no spoiler or no settings.
    if hook == "handle_item_tracking" and not (itemlog.has_spoiler and player.settings):
        return item.name
    if hook == "handle_location_tracking" and not (itemlog.has_spoiler and player.settings):
        return item.location.name
    if hook == "handle_state_tracking" and not player._super.has_spoiler:
        return (getattr(player.stats, "goal_str", None), dict(vars(player.stats)))

    namespace = dict(vars(u))
    namespace.update(
        player=player, item=item, ItemObject=item, location=location, itemlog=itemlog,
        game=itemlog, use_everywhere=False, settings=player.settings, slot_data=player.slot_data,
    )
    if hook == "handle_location_hinting":
        # the inline arm edits locals owned by the enclosing function, so recreate them
        requirements: Dict[str, Any] = {}
        costs: Dict[str, Any] = {}
        extra_info = ""

        def add_requirement(text: str) -> None:
            from cmds.ap_scripts.requirements import parse_requirement_string

            parsed = parse_requirement_string(text)
            if parsed is None:
                requirements[str(text)] = None
            else:
                name, count = parsed
                prev = requirements.get(name)
                requirements[name] = count if prev is None else prev + count

        namespace.update(
            requirements=requirements, costs=costs, extra_info=extra_info,
            add_requirement=add_requirement, l=location,
        )
        exec(_wrap(body, "", "", namespace), namespace)
        namespace["_arm"]()
        return (dict(requirements), extra_info, dict(costs))

    prelude = HOOK_PRELUDE.get(hook, "")
    postlude = HOOK_POSTLUDE.get(hook, "")
    exec(_wrap(body, prelude, postlude, namespace), namespace)
    result = namespace["_arm"]()
    if hook == "handle_state_tracking":
        return (getattr(player.stats, "goal_str", None), dict(vars(player.stats)))
    return result


def _run_new(hook: str, game: str, env) -> Any:
    from cmds.ap_scripts.game_handlers import get_handler_fuzzy

    handler = get_handler_fuzzy(game)
    if handler is not None and not hasattr(handler, hook):
        # The module was edited during this run, so the imported class predates the new method.
        import importlib

        module = importlib.reload(sys.modules[handler.__class__.__module__])
        handler = getattr(module, "handler", handler)
    if handler is None:
        # A module created during this run is not in the registry yet (the package already imported).
        import importlib

        from cmds.ap_scripts.game_handlers import register_handler

        module = importlib.import_module(f".{slug(game)}", __package__)
        candidate = getattr(module, "handler", None)
        if candidate is not None:
            register_handler(candidate)
            handler = get_handler_fuzzy(game)
    if handler is None:
        raise SystemExit(f"no handler registered for {game!r}")
    u, player, item, location, itemlog = env

    # Mirrors where the dispatch sites sit: both return before the handler when there is no spoiler
    # or no settings, so the handler must not be asked in those states either.
    if hook == "handle_item_tracking" and not (itemlog.has_spoiler and player.settings):
        return item.name
    if hook == "handle_location_tracking" and not (itemlog.has_spoiler and player.settings):
        return item.location.name
    if hook == "handle_state_tracking" and not player._super.has_spoiler:
        return (getattr(player.stats, "goal_str", None), dict(vars(player.stats)))
    if hook == "handle_location_hinting":
        out = handler.handle_location_hinting(player, location)
        if out is None:
            return ({}, "", {})
        return (dict(out[0] or {}), out[1] or "", dict(out[2] or {}) if len(out) > 2 else {})
    if hook == "handle_item_tracking":
        return handler.handle_item_tracking(itemlog, player, item)
    if hook == "handle_location_tracking":
        return handler.handle_location_tracking(itemlog, player, item, False)
    if hook == "handle_state_tracking":
        handler.handle_state_tracking(player, itemlog)
        return (getattr(player.stats, "goal_str", None), dict(vars(player.stats)))
    if hook == "is_location_checkable":
        return handler.is_location_checkable(location)
    raise SystemExit(f"unknown hook {hook}")


def _same_failure(old: Any, new: Any) -> bool:
    """Both sides raising the same exception type is equivalence (their messages can differ)."""
    if not (isinstance(old, str) and isinstance(new, str)):
        return False
    old_match = re.match(r"<old raised (\w+)", old)
    new_match = re.match(r"<new raised (\w+)", new)
    return bool(old_match and new_match and old_match.group(1) == new_match.group(1))


def check(hook: str, game: str, source: Optional[str] = None, verbose: bool = True) -> List[str]:
    body = arm_source(hook, game, source)
    settings, slot_keys, counted = _collect_keys(body)
    mismatches = []
    combos = _combinations(settings, slot_keys, counted)
    for combo in combos:
        s, sd, counts = combo
        old_env = _build_env(game, s, sd, counts)
        new_env = _build_env(game, s, sd, counts)
        before = copy.deepcopy(getattr(old_env[1].stats, "goal_str", None))
        try:
            old = _run_old(hook, game, body, old_env)
        except Exception as exc:  # the arm itself failing is worth knowing, but not a migration bug
            old = f"<old raised {type(exc).__name__}: {exc}>"
        try:
            new = _run_new(hook, game, new_env)
        except Exception as exc:
            new = f"<new raised {type(exc).__name__}: {exc}>"
        if old != new and not _same_failure(old, new):
            mismatches.append(f"settings={s} slot_data={sd} counts={counts}: old={old!r} new={new!r}")
    if verbose:
        print(f"{hook} / {game}: {len(combos)} case(s), {len(mismatches)} mismatch(es)")
        for line in mismatches[:12]:
            print("  DIFF:", line)
        print(f"  free names: {free_names(body, HOOKS[hook][1])}")
    return mismatches


# --------------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------------

def cmd_list(hook: str) -> None:
    func_name = HOOKS[hook][0]
    tree = ast.parse(old_source())
    func = _function_node(tree, func_name)
    games = sorted(_cases_by_game(func))
    print(f"{hook} ({func_name}): {len(games)} inline game(s)")
    for game in games:
        print("  ", game)


def cmd_show(hook: str, game: str) -> None:
    body = arm_source(hook, game)
    print(body)
    print("\n# free names:", free_names(body, HOOKS[hook][1]))


def module_path_for(game: str) -> pathlib.Path:
    """The module that already handles this game, else the slug-based file we would create."""
    from cmds.ap_scripts.game_handlers import get_handler_fuzzy

    handler = get_handler_fuzzy(game)
    if handler is not None:
        module = sys.modules.get(handler.__class__.__module__)
        if module is not None and getattr(module, "__file__", None):
            return pathlib.Path(module.__file__)
    return module_path(game)


def cmd_migrate(hook: str, game: str) -> int:
    body = arm_source(hook, game)
    free = free_names(body, HOOKS[hook][1])
    unknown = [n for n in free if n not in HOOK_ALLOWED.get(hook, set())]
    if hook == "handle_location_hinting":
        print(f"MANUAL: {game} / {hook} needs the requirements-dict translation — do it by hand")
        return 2
    if unknown:
        print(f"MANUAL: {game} / {hook} reads {unknown} — migrate this one by hand")
        return 2
    path = module_path_for(game)
    if path.exists() and f"def {hook}(" in path.read_text(encoding="utf-8"):
        # already migrated earlier in this run (or a previous one): verify only
        mismatches = check(hook, game, verbose=False)
        if mismatches:
            print(f"FAILED equivalence for {game} / {hook}:")
            for line in mismatches[:8]:
                print("  DIFF:", line)
            return 1
        print(f"OK {game} / {hook}: already migrated, equivalence clean")
        return 0
    if path.exists():
        append_hook(path, game, hook, body, free)
        print(f"appended {hook} to {path.name}")
    else:
        path.write_text(generate_module(hook, game, body, free), encoding="utf-8")
        print(f"created {path.name}")
    delete_arm(hook, game)
    mismatches = check(hook, game, verbose=False)
    if mismatches:
        print(f"FAILED equivalence for {game} / {hook}:")
        for line in mismatches[:8]:
            print("  DIFF:", line)
        return 1
    print(f"OK {game} / {hook}: equivalence clean")
    return 0


def cmd_migrate_all(hook: str) -> None:
    tree = ast.parse(old_source())
    games = sorted(_cases_by_game(_function_node(tree, HOOKS[hook][0])))
    done, manual, failed = [], [], []
    for game in games:
        code = cmd_migrate(hook, game)
        if code == 0:
            done.append(game)
        elif code == 2:
            manual.append(game)
        else:
            failed.append(game)
    print(f"\n{len(done)} migrated, {len(manual)} need manual work, {len(failed)} failed equivalence")
    if manual:
        print("manual:", ", ".join(manual))
    if failed:
        print("failed:", ", ".join(failed))


def main(argv: Sequence[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 1
    command, *rest = argv[1:]
    if command == "list":
        cmd_list(rest[0])
        return 0
    if command == "show":
        cmd_show(rest[0], rest[1])
        return 0
    if command == "check":
        return 1 if check(rest[0], rest[1]) else 0
    if command == "migrate":
        return cmd_migrate(rest[0], rest[1])
    if command == "migrate-all":
        cmd_migrate_all(rest[0])
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
