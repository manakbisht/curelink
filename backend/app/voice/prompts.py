"""Everything the bot says.

Each bot utterance is a host reaction (LLM-generated, see host.py) followed
by the game facts built here. Facts (cards, round, score, rules) are always
spoken from these deterministic templates, never from the LLM.
"""

HOST_SYSTEM_PROMPT = """\
You are the warm, upbeat host of a voice memory game called Memory Cards.
Reply with ONE short spoken line of at most 15 words reacting to the event you are given.
Rules:
- Plain conversational text only: no emojis, markdown, lists or quotation marks.
- Never mention specific words, sequences, scores, points, round numbers or rules.
  The game announces all of those right after your line.
- Do not ask the player questions.
"""

_EVENT_DESCRIPTIONS = {
    "greeting": "A new game is starting. Welcome the player by name.",
    "correct": "The player just repeated the whole sequence correctly and moves on to a longer one.",
    "wrong": "The player just got the sequence wrong, so the game is over. Be kind.",
    "completed": "The player cleared the final round and won the game. Celebrate.",
    "repeat": "The player asked to hear the sequence again. Reassure them briefly.",
}


def host_event(event: str, player_name: str) -> str:
    return f"Player name: {player_name}\nEvent: {_EVENT_DESCRIPTIONS[event]}"


def join_words(words: list[str]) -> str:
    return ", ".join(words)


def rules() -> str:
    return "I'll read out some words. Say them back to me in the same order. Say 'repeat' to hear them again."


def present_sequence(round_number: int, words: list[str]) -> str:
    return f"Round {round_number}. Your {len(words)} words are: {join_words(words)}. Your turn."


def points_earned(points: int) -> str:
    return f"That's {points} more points."


def reveal(expected: list[str], score: int) -> str:
    return f"The words were: {join_words(expected)}. You finished with {score} points."


def final_score(score: int) -> str:
    return f"You finished with {score} points."


def game_already_over() -> str:
    return "This game has already finished. Start a new game to play again."
