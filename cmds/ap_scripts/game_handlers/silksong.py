"""
Hollow Knight: Silksong — resolve `conditional progression` items from the slot's own options.

Migrated from the inline block in ap_itemlog.py's `live_classification()`. Silksong v0.4.5 declares
these items at one value and re-grades them at generation time — see silksong/__init__.py
create_item() lines 773-842. Each branch repeats the world's own condition and reads the option by
display name from the slot's settings, so a setting the log could not read falls back to the value
the world's default would have produced, never to a guess.

Verified against 7 generated option sets when the rule was first written: the resolved value matched
the flag the world actually assigned for all 247 conditional item-instances.
"""

from __future__ import annotations

from cmds.ap_scripts.game_handlers import (
    PROTOCOL_VERSION,
    ClassificationProposal,
    HandlerContext,
)

# 15 scrounge relics. Mirrors the world's own table.
SCROUNGE_RELICS = frozenset({
    'Relic: Weaver Effigy (Keelal, Shellwood)',
    'Relic: Bone Scroll (Wisp Thicket)',
    'Relic: Weaver Effigy (Camora, Moss Grotto)',
    'Relic: Choral Commandment (Jubilana)',
    'Relic: Rune Harp (High Halls)',
    'Relic: Choral Commandment (Western Whiteward)',
    'Relic: Rune Harp (Weavenest Cindril)',
    'Relic: Rune Harp (Weavenest Atla)',
    'Relic: Bone Scroll (Underworks)',
    'Relic: Bone Scroll (Far Fields)',
    'Relic: Choral Commandment (Moss Grotto)',
    'Relic: Choral Commandment (Eastern Whiteward)',
    'Relic: Bone Scroll (Greymoor)',
    'Relic: Weaver Effigy (Atla, The Slab)',
    'Relic: Arcane Egg',
})
# The 21 individual turn-ins = the 15 scrounge relics + the five Psalm Cylinders.
# 'Relic: Sacred Cylinder' is deliberately absent: the world declares it progression and its branch
# sets progression again, so its stored classification is a plain progression.
RELIC_TURN_INS = SCROUNGE_RELICS | frozenset({
    'Relic: Psalm Cylinder (East Whispering Vaults)',
    'Relic: Psalm Cylinder (Grindle)',
    'Relic: Psalm Cylinder (High Halls)',
    'Relic: Psalm Cylinder (Underworks)',
    'Relic: Psalm Cylinder (Vaultkeeper Cardinius)',
})
CREST_SLOT_ITEMS = frozenset({
    'Crest Slot: Architect (Blue 1)',
    'Crest Slot: Architect (Blue 2)',
    'Crest Slot: Architect (Yellow 1)',
    'Crest Slot: Architect (Yellow 2)',
    'Crest Slot: Beast (Yellow 1)',
    'Crest Slot: Beast (Yellow 2)',
    'Crest Slot: Shaman (Blue 1)',
    'Crest Slot: Shaman (Blue 2)',
    'Crest Slot: Wanderer (Blue 1)',
    'Crest Slot: Wanderer (Blue 2)',
})


class SilksongHandler:
    game = "Hollow Knight: Silksong"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"resolve_classification"})

    def resolve_classification(
        self, ctx: HandlerContext, item, stored=None
    ) -> ClassificationProposal | None:
        name = getattr(item, "name", "") or ""
        goal = str(ctx.setting("Goal", "")).lower().replace(" ", "_")
        relic_mode = str(ctx.setting("Relic Randomization", "vanilla")).lower()
        crest_mode = str(ctx.setting("Crest Slot Randomization", "vanilla")).lower()
        locket_mode = str(ctx.setting("Memory Locket Randomization", "vanilla")).lower()

        if name == "Scuttlebrace":
            # __init__.py:792-796
            return self._propose(
                "progression" if ctx.setting("Scuttlebrace Logic") else "useful",
                "world gates Scuttlebrace on Scuttlebrace Logic",
            )
        if name == "Simple Key (Green Prince)":
            # __init__.py:797-801 - only the Act 3 goal gates on it
            return self._propose(
                "progression" if goal == "act_3" else "useful",
                "world gates this on the Act 3 goal",
            )
        if name.startswith("Letter: "):
            # __init__.py:780-786 - letters stay progression except under the Spelling Bee goal,
            # where only the letters in the phrase are needed
            phrase = str(ctx.setting("Spelling Bee Phrase", "") or "").upper()
            letter = name.removeprefix("Letter: ").upper()
            return self._propose(
                "useful" if goal == "spelling_bee" and letter not in phrase else "progression",
                "Spelling Bee goal only needs the letters in the phrase",
            )
        if name in CREST_SLOT_ITEMS:
            # __init__.py:802-815
            return self._propose(
                "useful"
                if crest_mode == "vanilla"
                or (crest_mode == "shuffle" and locket_mode == "vanilla")
                else "progression",
                "crest-slot items only gate progression outside vanilla/shuffle-with-vanilla-lockets",
            )
        if name in RELIC_TURN_INS:
            # __init__.py:816-826 - a scrounge relic counts once Relic Randomization is not vanilla;
            # the individual turn-ins option covers all 21 of them
            promoted = bool(ctx.setting("Individual Relic Turn-ins")) or (
                name in SCROUNGE_RELICS and relic_mode != "vanilla"
            )
            return self._propose(
                "progression" if promoted else "useful",
                "relic turn-in counts once relic randomization or individual turn-ins are on",
            )
        return None

    @staticmethod
    def _propose(classification: str, reason: str) -> ClassificationProposal:
        return ClassificationProposal(
            classification=classification, reason=reason, source="silksong"
        )


handler = SilksongHandler()
