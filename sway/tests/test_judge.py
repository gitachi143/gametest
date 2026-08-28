"""The judge, and - more importantly - what happens when it is unavailable."""
import pytest

from app.ai import judge, prompts
from app.content import card, mind as get_mind

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_similarity_catches_a_lightly_edited_resend():
    a = "Let me through the door, I only need four minutes upstairs."
    b = "Let me through that door; I only need four minutes upstairs, please."
    assert judge.similarity(a, b) > 0.6
    assert judge.similarity(a, "The registrar signed it in 1998.") < 0.2


def test_repeat_detection_only_looks_at_the_players_own_lines():
    history = [{"who": "mind", "text": "Staff only. You are not staff."},
               {"who": "player", "text": "I only need four minutes upstairs."}]
    assert judge.repeats("I only need four minutes upstairs!", history)
    assert not judge.repeats("Staff only. You are not staff.", history)


def test_word_count_contracts_are_absolute():
    assert judge._grade("perfect-word", "Open the door.", []) == 2
    assert judge._grade("perfect-word", "Please would you open the door for me now", []) == 0
    assert judge._grade("silence", "...", []) == 2
    assert judge._grade("silence", " ".join(["word"] * 30), []) == 0


def test_keystone_needs_exactly_one_sentence():
    assert judge._grade("keystone", "You and I both know this door opens either way tonight.", []) == 2
    assert judge._grade("keystone", "You know it. So do I. Open it.", []) == 0


def test_callback_requires_something_said_earlier():
    history = [{"who": "mind", "text": "I have worked nights for six years."}]
    assert judge._grade("callback", "Six years of nights and nobody noticed.", history) == 0
    assert judge._grade("callback", "You have worked nights for six years and nobody noticed.",
                        history) == 2


def test_heuristic_never_mints_a_crit():
    cards = [card("flatter"), card("press"), card("anecdote")]
    long_message = ("In 1998 I watched Mirielle Vance sign three of these, and every one of "
                    "them held up. I am asking for the fourth. Do you want the reference?")
    verdict = judge.heuristic(long_message, cards, [])
    assert max(verdict["exec"].values()) <= 2
    assert verdict["persuasion"] <= 7
    assert verdict["fallback"] is True


def test_heuristic_punishes_over_explaining_in_a_holdout():
    long_reply = " ".join(["I only mean that the whole situation was complicated"] * 12)
    held = judge.heuristic("No.", [], [], holdout=True)
    spilled = judge.heuristic(long_reply, [], [], holdout=True)
    assert held["persuasion"] > spilled["persuasion"]


def test_tags_are_inferred_from_prose_so_cardless_play_still_hits_traits():
    tags = judge.infer_tags("I respect the work you have done here more than you think.", [])
    assert "flatter" in tags
    assert "authority" in judge.infer_tags("Section four of the policy says otherwise.", [])
    assert "trade" in judge.infer_tags("Let me give you something first.", [])


def test_normalise_clamps_everything_hostile():
    cards = [card("flatter")]
    out = judge.normalise({
        "persuasion": 999, "exec": [{"card": "flatter", "grade": 77},
                                    {"card": "not-a-card", "grade": 3}],
        "flags": ["repeat", "invented"], "tags": ["flatter", "invented"],
        "read": "x" * 400,
    }, cards)
    assert out["persuasion"] == 10
    assert out["exec"] == {"flatter": 3}
    assert out["flags"] == ["repeat"] and out["tags"] == ["flatter"]
    assert len(out["read"]) <= judge.READ_LIMIT + 1


def test_a_read_that_just_echoes_the_player_is_dropped():
    """Observed live: the judge occasionally returns the input as the read."""
    message = "That is a question about the fourteenth dressed up as small talk."
    assert judge.clean_read(message, message) == ""
    assert judge.clean_read("Over-explained; you handed her a date.", message)


def test_a_long_read_is_trimmed_at_a_word_boundary():
    read = judge.clean_read("The player conceded ground by " + "explaining " * 30, "x")
    assert read.endswith("…") and "explainin…" not in read
    assert len(read) <= judge.READ_LIMIT + 1


def test_normalise_treats_an_ungraded_card_as_a_fumble():
    cards = [card("flatter"), card("press")]
    out = judge.normalise({"persuasion": 5, "exec": [{"card": "flatter", "grade": 2}]}, cards)
    assert out["exec"] == {"flatter": 2, "press": 0}


def test_normalise_tolerates_a_dict_shaped_exec():
    out = judge.normalise({"persuasion": 4, "exec": {"flatter": 2}}, [card("flatter")])
    assert out["exec"] == {"flatter": 2}


def test_normalise_survives_garbage():
    out = judge.normalise({"persuasion": "lots", "exec": "no", "flags": None,
                           "tags": 7, "read": None}, [])
    assert out["persuasion"] == 0 and out["exec"] == {} and out["read"] == ""


async def test_demo_mode_always_returns_an_honest_fallback():
    verdict = await judge.judge_turn(
        mind=get_mind("doorman"), objective="let you in", history=[],
        message="Nineteen years and no trouble. That is you, not the lock.",
        cards=[card("flatter")],
    )
    assert verdict["fallback"] is True
    assert 0 <= verdict["persuasion"] <= 10
    assert set(verdict["exec"]) == {"flatter"}


async def test_a_broken_model_degrades_instead_of_raising():
    """The judge is the one call a turn cannot proceed without."""
    from app.ai import client as clientmod
    from app.config import settings

    class Broken:
        name, model = "broken", "broken"

        async def complete(self, req):
            raise clientmod.LLMError("429 exhausted")

        async def stream(self, req):
            raise clientmod.LLMError("429 exhausted")
            yield ""

        async def aclose(self):
            pass

    was = settings.provider
    settings.provider = "vertex"
    clientmod.set_client(Broken())
    try:
        verdict = await judge.judge_turn(
            mind=get_mind("doorman"), objective="let you in", history=[],
            message="A perfectly reasonable request, made politely.", cards=[card("press")])
    finally:
        clientmod.set_client(None)
        settings.provider = was
    assert verdict["fallback"] is True, "a throttled judge must say so"
    assert verdict["persuasion"] <= 7


async def test_a_lying_model_cannot_award_itself_a_crit_on_the_fallback_path():
    """If the call failed, grades come from Python - not from a stale payload."""
    from app.ai import client as clientmod
    from app.config import settings

    class Liar:
        name, model = "liar", "liar"

        async def complete(self, req):
            return '{"persuasion": 10, "exec": [{"card": "press", "grade": 3}], ' \
                   '"flags": [], "tags": [], "read": "flawless", "_fallback": true}'

        async def stream(self, req):
            yield ""

        async def aclose(self):
            pass

    was = settings.provider
    settings.provider = "vertex"
    clientmod.set_client(Liar())
    try:
        verdict = await judge.judge_turn(
            mind=get_mind("doorman"), objective="let you in", history=[],
            message="short", cards=[card("press")])
    finally:
        clientmod.set_client(None)
        settings.provider = was
    assert verdict["exec"]["press"] < 3


def test_the_two_rubrics_declare_the_same_output_shape():
    for schema in (prompts.JUDGE_SCHEMA, prompts.HOLDOUT_SCHEMA):
        for key in ("persuasion", "exec", "flags", "tags", "read"):
            assert key in schema["properties"]
            assert key in schema["required"]
    assert "leaked" in prompts.HOLDOUT_SCHEMA["properties"]


def test_the_character_is_never_told_the_mechanics():
    """The mind performs; it must not know it is inside a scoring system."""
    mind = get_mind("doorman")
    system = prompts.mind_system(
        mind, objective=mind["objective"], pressure_note=prompts.pressure_note(0.5),
        verdict_note=prompts.verdict_note(6, 0, []),
        tactic_note=prompts.tactic_note([{"id": "flatter", "name": "Flatter"}], {"flatter": 2}, False))
    low = system.lower()
    for banned in ("resistance", "multiplier", "persuasion", "damage", "focus",
                   "momentum", "exec", "grade", "0-10"):
        assert banned not in low, banned
    assert "never mention the game" in low, "and it must be told not to break frame"
    # Game state reaches the character only as prose, never as a figure.
    qualitative = [prompts.pressure_note(f) for f in (0.9, 0.5, 0.2, 0.0)]
    qualitative += [prompts.verdict_note(p, 0, []) for p in range(11)]
    qualitative += [prompts.composure_note(p, False) for p in range(11)]
    qualitative += [prompts.resolve_note(f) for f in (0.9, 0.5, 0.2, 0.0)]
    for note in qualitative:
        assert note and not any(ch.isdigit() for ch in note), note


def test_the_mask_hides_the_tactic_note_entirely():
    assert prompts.tactic_note([{"id": "bluff", "name": "Bluff"}], {"bluff": 2}, True) == ""
    assert "bluff" in prompts.tactic_note([{"id": "bluff", "name": "Bluff"}], {"bluff": 2}, False)


def test_judge_prompt_carries_every_contract_it_must_grade():
    cards = [card("flatter"), card("keystone")]
    body = prompts.judge_user(mind=get_mind("doorman"), objective="let you in",
                              history=[{"who": "player", "text": "hello"}],
                              message="hi", cards=cards)
    for c in cards:
        assert c["contract"] in body and c["id"] in body
    assert "let you in" in body


def test_judge_prompt_says_so_when_no_tactics_were_committed():
    body = prompts.judge_user(mind=get_mind("doorman"), objective="x", history=[],
                              message="hi", cards=[])
    assert "empty array" in body
