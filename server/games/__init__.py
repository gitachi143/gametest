"""Game registry. Adding a game means adding one module and one line here."""
from __future__ import annotations

from . import coldcase, crossfire, gauntlet, hotwire, oracle, sleeper, vault

REGISTRY = {
    g.meta.id: g
    for g in (
        vault.GAME,
        oracle.GAME,
        hotwire.GAME,
        coldcase.GAME,
        sleeper.GAME,
        crossfire.GAME,
        gauntlet.GAME,
    )
}

# Home-screen ordering: the merged mode sits last, as the boss door.
ORDER = ["vault", "coldcase", "hotwire", "sleeper", "oracle", "crossfire", "gauntlet"]


def get_game(game_id: str):
    return REGISTRY.get(game_id)


def catalogue() -> list[dict]:
    return [REGISTRY[gid].meta.to_dict() for gid in ORDER if gid in REGISTRY]
