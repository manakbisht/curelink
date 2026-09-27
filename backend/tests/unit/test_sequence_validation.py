import itertools
import random

import pytest

from app.services.game_service import (
    CARDS,
    FILLER_WORDS,
    evaluate_response,
    generate_sequence,
    normalize_transcript,
    points_for_round,
    sequence_length_for_round,
    words_match,
)

EXPECTED = ["apple", "train", "moon", "coffee"]


class TestEvaluateResponse:
    def test_exact_sequence_is_correct(self):
        assert evaluate_response(EXPECTED, ["apple", "train", "moon", "coffee"])

    def test_raw_transcript_is_accepted(self):
        assert evaluate_response(EXPECTED, "apple train moon coffee")

    def test_different_word_is_wrong(self):
        assert not evaluate_response(EXPECTED, "apple train sun coffee")

    def test_wrong_order_is_wrong(self):
        assert not evaluate_response(EXPECTED, "apple moon train coffee")

    def test_missing_word_is_wrong(self):
        assert not evaluate_response(EXPECTED, "apple train moon")

    def test_extra_word_is_wrong(self):
        assert not evaluate_response(EXPECTED, "apple train moon coffee tiger")

    def test_empty_answer_is_wrong(self):
        assert not evaluate_response(EXPECTED, "")

    def test_case_is_ignored(self):
        assert evaluate_response(EXPECTED, "Apple TRAIN Moon coffee")

    def test_punctuation_is_ignored(self):
        assert evaluate_response(EXPECTED, "Apple, train... moon; coffee!")

    def test_filler_words_are_ignored(self):
        assert evaluate_response(EXPECTED, "um, apple and then train, uh, the moon and coffee")

    def test_plural_transcription_is_tolerated(self):
        assert evaluate_response(["pencil", "tomato"], "pencils tomatoes")

    def test_minor_misspelling_is_tolerated(self):
        assert evaluate_response(["violin", "banana"], "violen bananna")

    def test_expected_is_case_insensitive(self):
        assert evaluate_response(["Apple", "Moon"], "apple moon")


class TestNormalization:
    def test_normalize_transcript(self):
        assert normalize_transcript("  Apple,  TRAIN...and the moon!  ") == ["apple", "train", "moon"]

    def test_short_words_need_exact_match(self):
        assert not words_match("sun", "son")


class TestCards:
    def test_cards_are_unique(self):
        assert len(set(CARDS)) == len(CARDS)

    def test_no_card_is_a_filler_word(self):
        assert not FILLER_WORDS.intersection(CARDS)

    def test_distinct_cards_never_match_each_other(self):
        collisions = [(a, b) for a, b in itertools.permutations(CARDS, 2) if words_match(a, b)]
        assert collisions == []


class TestDifficulty:
    @pytest.mark.parametrize(("round_number", "length"), [(1, 2), (2, 3), (3, 4), (4, 5), (10, 11)])
    def test_sequence_grows_by_one_each_round(self, round_number, length):
        assert sequence_length_for_round(round_number) == length

    def test_round_zero_is_invalid(self):
        with pytest.raises(ValueError):
            sequence_length_for_round(0)

    def test_generated_sequence_has_distinct_known_cards(self):
        sequence = generate_sequence(5, random.Random(7))
        assert len(sequence) == 6
        assert len(set(sequence)) == 6
        assert set(sequence) <= set(CARDS)

    def test_generation_is_reproducible_with_a_seed(self):
        assert generate_sequence(3, random.Random(1)) == generate_sequence(3, random.Random(1))

    def test_points_scale_with_sequence_length(self):
        assert points_for_round(1, 10) == 20
        assert points_for_round(3, 10) == 40
