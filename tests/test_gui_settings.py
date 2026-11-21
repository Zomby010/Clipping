"""Tests for the Clipping Studio app's settings logic (no screen needed)."""

import pytest

from clipping.config import _build_parser
from clipping.gui import settings as S


@pytest.fixture(scope="module")
def fields():
    return S.get_fields()


def test_every_main_option_is_in_the_app(fields):
    parser = _build_parser()
    dests = {a.dest for a in parser._actions if a.option_strings} - S.SKIP
    assert {f.dest for f in fields} == dests
    assert all(f.section in S.SECTIONS for f in fields)


def test_default_command_is_accepted_by_main(fields):
    cmd = S.build_command(fields, S.default_values(fields), ["https://youtu.be/abc"], python="py")
    assert cmd[:2] == ["py", "-u"]
    args = _build_parser().parse_args(cmd[3:])
    assert args.url == ["https://youtu.be/abc"]
    assert args.clips == 3
    assert (args.min_length, args.max_length) == (15, 60)
    assert args.voiceover is True
    assert args.whisper_device == "cpu"
    assert args.no_bgm is False


def test_unticked_positive_box_passes_the_no_flag(fields):
    values = S.default_values(fields)
    values["no_bgm"] = False        # "Background music" unticked
    values["no_reactions"] = True   # "Reaction clips" ticked (default)
    cmd = S.build_command(fields, values, ["u"])
    assert "--no-bgm" in cmd
    assert "--no-reactions" not in cmd


def test_values_equal_to_main_defaults_are_left_out(fields):
    values = S.default_values(fields)
    values["ratio"] = "9:16"
    values["original_volume"] = "0.30"
    cmd = S.build_command(fields, values, ["u"])
    assert "--ratio" not in cmd
    assert "--original-volume" not in cmd


def test_changed_values_round_trip_through_main_parser(fields):
    values = S.default_values(fields)
    values.update({"ratio": "16:9", "original_volume": "0.2", "max_reactions": "1",
                   "text": "My Channel", "watermark": True, "voiceover_voice": "en-KE-AsiliaNeural"})
    args = _build_parser().parse_args(S.build_command(fields, values, ["a", "b"])[3:])
    assert args.ratio == "16:9"
    assert args.original_volume == pytest.approx(0.2)
    assert args.max_reactions == 1
    assert args.text == "My Channel"
    assert args.watermark is True
    assert args.voiceover_voice == "en-KE-AsiliaNeural"
    assert args.url == ["a", "b"]


def test_bad_number_and_missing_link_are_reported(fields):
    values = S.default_values(fields)
    with pytest.raises(S.SettingError, match="link"):
        S.build_command(fields, values, ["  "])
    values["clips"] = "three"
    with pytest.raises(S.SettingError, match="Number of clips"):
        S.build_command(fields, values, ["u"])


def test_settings_and_profiles_round_trip(tmp_path, fields):
    path = str(tmp_path / "s.json")
    data = S.load_settings(fields, path)
    data["values"]["clips"] = "5"
    data["profiles"]["GTA 6"] = {"ratio": "9:16"}
    S.save_settings(data, path)
    again = S.load_settings(fields, path)
    assert again["values"]["clips"] == "5"
    assert again["profiles"] == {"GTA 6": {"ratio": "9:16"}}


def test_env_keys_keep_other_lines(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# comment\nGOOGLE_API_KEY=your-gemini-api-key-here\nOTHER=1\n")
    assert S.is_placeholder(S.read_env(str(env))["GOOGLE_API_KEY"])
    S.write_env({"GOOGLE_API_KEY": "abc", "PEXELS_API_KEY": "p"}, str(env))
    text = env.read_text()
    assert "# comment" in text and "OTHER=1" in text
    assert S.read_env(str(env)) == {"GOOGLE_API_KEY": "abc", "OTHER": "1", "PEXELS_API_KEY": "p"}


def test_clip_length_presets_are_valid():
    for lo, hi in S.CLIP_LENGTHS.values():
        assert 5 <= lo < hi <= 179


def test_output_splitter_handles_progress_and_lines():
    from clipping.gui.settings import OutputSplitter
    s = OutputSplitter()
    out = []
    for chunk in [b"hello\n 10%\r 20%\r", b" 30%\r\ndone\nta", b"il"]:
        out += s.feed(chunk)
    out += s.flush()
    assert out == [("line", "hello"), ("progress", " 10%"), ("progress", " 20%"),
                   ("line", " 30%"), ("line", "done"), ("line", "tail")]
