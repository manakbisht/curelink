"""Everything the bot says.

Lines that carry game facts (the cards, the round, the score) are built
here deterministically so they are always spoken exactly.
"""


def join_words(words: list[str]) -> str:
    return ", ".join(words)


def greeting(player_name: str) -> str:
    return (
        f"Hi {player_name}, welcome to Memory Cards! I'll read out some words. "
        "Say them back to me in the same order. Say 'repeat' if you need to hear them again."
    )


def present_sequence(round_number: int, words: list[str]) -> str:
    return f"Round {round_number}. Your {len(words)} words are: {join_words(words)}. Your turn."


def correct(points: int) -> str:
    return f"Correct! That's {points} more points."


def wrong(expected: list[str], score: int) -> str:
    return f"Oh no, not quite. The words were: {join_words(expected)}. You finished with {score} points."


def completed(score: int) -> str:
    return f"Incredible, you cleared every round! You finished with {score} points."


def repeat() -> str:
    return "Sure, here they are again."


def game_already_over() -> str:
    return "This game has already finished. Start a new game to play again."
