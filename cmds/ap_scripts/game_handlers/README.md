# Game handlers

Game-specific itemlog logic lives in this directory, one module per game, instead of in
`ap_itemlog.py` / `cmds/ap_scripts/utils.py`. The registry is `__init__.py`; the protocol it defines
is documented here.

## Writing a handler

```python
# cmds/ap_scripts/game_handlers/my_game.py
from cmds.ap_scripts.game_handlers import (
    PROTOCOL_VERSION, Announcement, ClassificationProposal, HandlerContext, PostDecision, SendEvent,
)

class MyGameHandler:
    game = "My Game"                                  # or ("My Game", "My Game (Variant)")
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"should_post_item_send", "on_item_send", "resolve_conditional_progression"})

    def should_post_item_send(self, ctx: HandlerContext, send: SendEvent) -> PostDecision | None:
        ...

handler = MyGameHandler()
```

Drop the file in and it is auto-registered on import — including a leading process (the module is
discovered by `pkgutil`, and modules starting with `_` are skipped so tooling like `_selfcheck.py`
does not register).

Rules of the road:

- **Return, don't mutate.** A hook returns a value to claim the decision, or `None` to fall through.
- **Returning `None` is the norm.** The default logic in `ap_itemlog.py` stays authoritative for
  games with no handler, so an unhandled game behaves exactly as before.
- **Never write to the database.** Classification hooks return a `ClassificationProposal`; the core
  applies it under the existing policy (fill blanks or raise, manual edits win, downgrades held for
  review). This is what makes a handler safe to ship and easy to roll back.
- **Be idempotent.** The logger resumes from a stored line number, so a crash mid-seed replays a
  prefix; give announcements and state changes a stable `ctx.dedupe_key(...)`.
- **Declare `implements`.** It lets the dispatcher skip building context for games that do not use a
  hook. A handler with no declaration is treated as legacy: candidate for every hook, no context.
- **Keep the table data out of the code** when it is big: a 300-line name/region table belongs in a
  data file next to the handler, not in Python.

## The context object

```python
HandlerContext(
    game, player, itemlog, slot, player_name, seed_name, room_id,
    datapackage_checksum, timestamp, item_index, settings,
)
```

`ctx` is frozen — a hook reads the world but cannot rewrite identity mid-dispatch. Useful members:

- `ctx.settings` / `ctx.setting("Goal")` — the slot's own resolved options, keyed by **display
  name** (the room's names, not the YAML keys). A missing setting should fall back to the world's
  default, never to a guess.
- `ctx.player` — the live `Player` object (for `inventory`, `spoilers`, `slot_data`).
- `ctx.state("my_game")` — a dict persisted for this handler via `Player.stats`, so conditions
  survive a restart instead of living in a module global.
- `ctx.dedupe_key(*extra)` — stable key for "act once per thing".

## The hooks

### `should_post_item_send` — the "important location" override

The default rule suppresses filler and currency sends (Spotzone vote, 2026-01-15 amendment). This
hook lets a game post anyway when the *location* matters. Positional args: `(ctx, send)`, where
`send` carries `sender`, `receiver`, `item`, `location`, `classification`, `is_filler`,
`is_currency`, `skip_msg`, `in_release`, `in_collect`.

Claiming a post does **not** bypass release/collect batching — the core still applies the buffers.
The first implementation is `spyro3.py` (Skill Point checks gate the 20-per-level goal).

### `resolve_conditional_progression` — reclassify at seed time

A row stored as `conditional progression` means "progression in some settings/seeds, otherwise
useful or filler". Resolve it from the seed's own options and return a `ClassificationProposal`;
return `None` to leave it conditional. Sketched from the Silksong migration:

```python
def resolve_conditional_progression(self, ctx, item, default="conditional progression"):
    if item.name == "Simple Key (Green Prince)":
        goal = str(ctx.setting("Goal", "")).lower().replace(" ", "_")
        return ClassificationProposal(
            classification="progression" if goal == "act_3" else "useful",
            reason="world gates this on the Act 3 goal",
            source="silksong",
        )
    return None
```

`downgrade=True` marks a proposal that would *lower* an existing stored value — those are held for
human review rather than applied.

### `on_item_send` — announce things

Return announcements; the core renders and posts them, so formatting, goaled-player dimming and
rate limiting stay in one place:

```python
def on_item_send(self, ctx, send):
    seen = ctx.state("my_game")
    if send.item_name == "Area Key" and not seen.get("area_unlocked"):
        seen["area_unlocked"] = True
        return [Announcement(
            kind="area_access",
            text=f"**{ctx.player_name}** unlocked the Sunken Area",
            dedupe_key=ctx.dedupe_key("area", "sunken"),
        )]
    return None
```

## Checking your work

```bash
python -m cmds.ap_scripts.game_handlers._selfcheck
python -m cmds.ap_scripts.game_handlers._selfcheck --corpus path/to/item-log.jsonl
```

The first checks the registry, calls every declared hook with a fixture, verifies chain order and
that a raising handler degrades to the default (`AP_HANDLERS_STRICT=1` re-raises instead). The
second reads an archived `#item-log` export and asks a migration question: every posted line whose
item carried a filler/currency icon is a line the default rule would have suppressed, so *some* rule
must explain it. Buckets are "claimed by a handler", "claimed by a still-inline rule", and
"unexplained" — the last bucket is the one to investigate, because a migrated rule must not lose a
line.

## Migrating a rule out of the core

1. Freeze the interface first (this file) — do not bulk-move onto an interface that is still moving.
2. Move **one** rule end-to-end. `spyro3.py` is the worked example: the inline branch was replaced by
   a dispatch, with the other two rules left in place behind `# TODO(handler):` comments.
3. Run the self-check, then `--corpus`, and compare before/after. The corpus is the safety net: a
   migration that loses a posted line shows up immediately.
4. Bulk-move the rest, one game per commit, with the same check.
5. Delete the inline branch only when the handler is registered *and* the corpus still explains every
   line it used to.

**Known gap:** the v1 call sites (`handle_item_tracking` et al. in `utils.py`) still call the handler
directly rather than through `dispatch()`, so they do not get error containment yet. Route them
through `dispatch()` as they are touched.
