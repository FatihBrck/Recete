import math

import pytest

from reader.confidence import ConfidenceMap, leaf_spans
from reader.llm_client import TokenLogprob


def toks(*pairs):
    return [TokenLogprob(t, math.log(p)) for t, p in pairs]


def test_leaf_spans_nested():
    text = '{"right":{"sph":{"raw":"-2,5","handwritten":true}},"brand":null}'
    s = leaf_spans(text)
    assert text[slice(*s["right.sph.raw"])] == "-2,5"
    assert text[slice(*s["right.sph.handwritten"])] == "true"
    assert "brand" in s
    assert text[slice(*s["brand"])] == "null"


def test_leaf_spans_escapes_and_numbers():
    text = '{"a": "x\\"y", "b": {"n": -1.5e2}, "c": []}'
    s = leaf_spans(text)
    assert text[slice(*s["a"])] == 'x\\"y'
    assert text[slice(*s["b.n"])] == "-1.5e2"
    assert "c" not in s


def test_leaf_spans_array_index_segment():
    text = '{"l":["a",1]}'
    s = leaf_spans(text)
    assert text[slice(*s["l.0"])] == "a"
    assert text[slice(*s["l.1"])] == "1"


def test_min_and_product():
    text = '{"raw":"-2,5"}'
    cm = ConfidenceMap(text, toks(('{"', .99), ("raw", .99), ('":"', .99), ("-", .6),
                                  ("2", .95), (",", .9), ("5", .8), ('"}', .99)))
    assert cm.get("raw") == pytest.approx(.6)
    assert cm.get("raw", mode="product") == pytest.approx(.6 * .95 * .9 * .8)


def test_byte_fragment_tokens_get_span_of_skipped_chars():
    text = '{"raw":"−2"}'
    cm = ConfidenceMap(text, toks(('{"', .99), ("raw", .99), ('":"', .99),
                                  ("�", .5), ("�", .7), ("2", .95), ('"}', .99)))
    assert cm.available
    assert cm.get("raw") == pytest.approx(.5)


def test_unalignable_tokens_mark_unavailable():
    cm = ConfidenceMap('{"raw":"12"}', toks(("garbage", .9)))
    assert not cm.available and cm.get("raw") == 0.0


def test_empty_tokens_unavailable_and_missing_path_zero():
    assert not ConfidenceMap('{"a":"1"}', []).available
    cm = ConfidenceMap('{"a":"1"}', toks(('{"a":"', .9), ("1", .9), ('"}', .9)))
    assert cm.get("nope") == 0.0


def test_trailing_fragments_get_remaining_span():
    text = '{"a":"−"}'
    cm = ConfidenceMap(text, toks(('{"a":"', .9), ("�", .4)))
    assert cm.available
    assert cm.get("a") == pytest.approx(.4)


def test_skipped_characters_without_token_is_unavailable():
    cm = ConfidenceMap('{"a":"12"}', toks(('{"a":"', .9), ("2", .9), ('"}', .9)))
    assert not cm.available


def test_untokenized_tail_is_unavailable():
    cm = ConfidenceMap('{"a":"12"}', toks(('{"a":"', .9), ("1", .9)))
    assert not cm.available


def test_confidence_clamped_to_unit_interval():
    text = '{"a":"1"}'
    cm = ConfidenceMap(text, [TokenLogprob('{"a":"', 0.0), TokenLogprob("1", 0.1),
                              TokenLogprob('"}', 0.0)])
    assert 0.0 <= cm.get("a") <= 1.0
    assert cm.get("a") == 1.0


def test_stray_token_before_exact_match_lowers_confidence():
    cm = ConfidenceMap('{"a":"1"}', toks(('{"a":"', .9), ("X", .05), ("1", .9), ('"}', .9)))
    assert cm.get("a") == pytest.approx(.05)


def test_stray_token_before_minus_lowers_confidence():
    cm = ConfidenceMap('{"a":"-2"}', toks(('{"a":"', .9), ("�", .05), ("-", .9),
                                          ("2", .9), ('"}', .9)))
    assert cm.get("a") == pytest.approx(.05)


def test_zero_length_token_does_not_resolve_pending():
    cm = ConfidenceMap('{"a":"−2"}', toks(('{"a":"', .9), ("�", .05), ("", .99),
                                          ("�", .9), ("2", .9), ('"}', .9)))
    assert cm.available
    assert cm.get("a") == pytest.approx(.05)


def test_long_trailing_pending_unavailable():
    cm = ConfidenceMap('{"a":"1234567"}', toks(('{"a":"', .9), ("�", .5)))
    assert not cm.available


def test_trailing_zero_length_token_keeps_confidence():
    cm = ConfidenceMap('{"a":"1"}', toks(('{"a":"', .9), ("1", .8), ('"}', .9), ("", .01)))
    assert cm.available
    assert cm.get("a") == pytest.approx(.8)


def test_product_across_fragment_span():
    cm = ConfidenceMap('{"a":"−2"}', toks(('{"a":"', .9), ("�", .5), ("�", .7),
                                          ("2", .95), ('"}', .9)))
    assert cm.get("a", mode="product") == pytest.approx(.5 * .7 * .95)


def test_leaf_spans_invalid_raises():
    with pytest.raises(ValueError):
        leaf_spans("[1}")
    with pytest.raises(ValueError):
        leaf_spans("[}")
