"""Clip Editor: typed times, ranges and timeline maths."""

from types import SimpleNamespace

import pytest

from clipping.editor import timeline as T


@pytest.mark.parametrize("text,seconds", [
    ("1:02", 62), ("1:02.5", 62.5), ("1:02:03", 3723), ("0:09", 9),
    ("1.02", 62), ("4.55", 295), ("62", 62), ("62s", 62), ("62.5s", 62.5), (" 2:38 ", 158),
])
def test_parse_time(text, seconds):
    assert T.parse_time(text) == pytest.approx(seconds)


@pytest.mark.parametrize("bad", ["", "abc", "1:75", "1.75", "1:02:03:04", "1::2", "-5s"])
def test_parse_time_rejects_nonsense(bad):
    with pytest.raises(T.TimeParseError):
        T.parse_time(bad)


def test_owner_example_gives_one_minute_eight():
    ranges = T.parse_ranges("1:02-1:10, 2:38-2:49, 4:06-4:55")
    assert ranges == [(62, 70), (158, 169), (246, 295)]
    segs = [SimpleNamespace(source_in=a, source_out=b, speed=1.0) for a, b in ranges]
    assert T.total_length(segs) == 68
    assert T.format_time(T.total_length(segs)) == "1:08"


def test_chat_style_dots_and_separators():
    assert T.parse_ranges("1.02-1.10; 2.38 to 2.49\n4.06 – 4.55") == [(62, 70), (158, 169), (246, 295)]


@pytest.mark.parametrize("bad", ["1:10-1:02", "1:02", "1:02-1:10-1:20", " , "])
def test_parse_ranges_rejects(bad):
    with pytest.raises(T.TimeParseError):
        T.parse_ranges(bad)


def test_format_time():
    assert T.format_time(0) == "0:00"
    assert T.format_time(3723) == "1:02:03"
    assert T.format_time(62.46, tenths=True) == "1:02.5"
    assert T.format_time(59.96, tenths=True) == "1:00.0"


def _segs(*items):
    return [SimpleNamespace(source_id="a", source_in=a, source_out=b, speed=s) for a, b, s in items]


def test_layout_and_mapping_with_speed():
    segs = _segs((10, 14, 1.0), (100, 104, 2.0), (50, 53, 1.0))  # 4 s + 2 s + 3 s
    pieces = T.layout(segs)
    assert [(p.edit_start, p.edit_end) for p in pieces] == [(0, 4), (4, 6), (6, 9)]
    assert T.total_length(segs) == 9
    assert T.edit_to_source(segs, 1.0) == (0, 11.0)
    assert T.edit_to_source(segs, 5.0) == (1, 102.0)  # 1 s into a 2x moment = 2 s of source
    assert T.edit_to_source(segs, 9.0) is None
    assert T.source_to_edit(segs, 1, 102.0) == 5.0
    assert T.source_to_edit(segs, 2, 999) == 9.0  # clamped to the moment's end


def test_length_report_and_description():
    rep = T.length_report(68)
    assert rep["over_target"] and rep["nearest_target"] == 60
    assert rep["platforms"]["YouTube Shorts"] and rep["platforms"]["Facebook Reels"]
    assert T.length_report(29.9)["nearest_target"] == 30
    assert "fits a 15 s Short" in T.describe_length(14)
    assert "Too long for Facebook Reels" in T.describe_length(120)
    assert "No moments" in T.describe_length(0)


def test_validate():
    segs = _segs((5, 3, 1.0), (0, 30, 1.0), (0, 2, 9.0))
    problems = T.validate(segs, {"a": 20})
    assert any("Moment 1" in p and "end is not after" in p for p in problems)
    assert any("Moment 2" in p and "only 0:20 long" in p for p in problems)
    assert any("Moment 3" in p and "speed" in p for p in problems)
