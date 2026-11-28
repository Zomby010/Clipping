"""Source view: playing a selected moment stops at the moment's finish time."""

from types import SimpleNamespace

from clipping.editor.gui.app import EditorApp


class FakePlayer:
    def __init__(self, path, t):
        self.path, self.t, self.playing = path, t, False

    def time(self):
        return self.t

    def seek(self, s):
        self.t = s

    def pause(self):
        self.playing = False

    def toggle(self):
        self.playing = not self.playing


class FakeRoot:
    def __init__(self):
        self.calls = []

    def after(self, ms, fn):
        self.calls.append((ms, fn))


def make(t, watch="source", seg=(5.0, 21.4)):
    s = SimpleNamespace(source_id="a", source_in=seg[0], source_out=seg[1])
    app = SimpleNamespace(
        project=SimpleNamespace(segments=[s], source=lambda i: SimpleNamespace(path="/v.mp4")),
        player=FakePlayer("/v.mp4", t), root=FakeRoot(),
        watch=SimpleNamespace(get=lambda: watch), selected_index=lambda: 0, _moment_end=None,
        _arm_cut_end=lambda: None, _cut_end=None)   # My cut has its own stop, tested in phase2_fixes
    for name in ("toggle_play", "_selected_moment_here", "_arm_moment_end", "_guard_moment_end"):
        setattr(app, name, getattr(EditorApp, name).__get__(app))
    return app


def test_play_inside_moment_stops_at_its_end():
    app = make(5.6)
    app.toggle_play()
    assert app.player.playing and app._moment_end == 21.4
    app.player.t = 21.5
    app.root.calls[-1][1]()          # the 30 ms guard fires
    assert not app.player.playing and app.player.t == 21.4 and app._moment_end is None


def test_play_at_the_end_replays_the_moment():
    app = make(21.4)
    app.toggle_play()
    assert app.player.t == 5.0 and app.player.playing and app._moment_end == 21.4


def test_playing_outside_the_moment_is_free():
    app = make(40.0)
    app.toggle_play()
    assert app.player.playing and app._moment_end is None and not app.root.calls


def test_pausing_cancels_the_guard():
    app = make(6.0)
    app.toggle_play()
    app.player.playing = False
    app.root.calls[-1][1]()
    assert app._moment_end is None


def test_cut_view_leaves_the_source_guard_alone():
    app = make(6.0, watch="cut")
    app.toggle_play()
    assert app._moment_end is None
