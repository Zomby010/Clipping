"""Phase A: easier editing. Drag to trim / move / zoom on the timeline, faster
previews from cached moments, Place things on the video, drag a reaction in."""

import os
import subprocess

import pytest

from clipping.editor import media, overlays as O, placement as PL, project as P, render, text_layers as T, timeline
from test_editor_phase2_fixes import app  # noqa: F401  (window fixture)


def _ff(path, *args):
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", *args, str(path)], check=True)
    return str(path)


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    folder = tmp_path_factory.mktemp("a")
    # colour changes every 2 s so frames can be told apart; a tone for the sound
    return _ff(folder / "src.mp4", "-f", "lavfi", "-i",
               "color=c=red:s=320x568:d=2:r=30[a];color=c=green:s=320x568:d=2:r=30[b];"
               "color=c=blue:s=320x568:d=2:r=30[c];color=c=white:s=320x568:d=2:r=30[d];"
               "[a][b][c][d]concat=n=4:v=1:a=0", "-f", "lavfi", "-i", "sine=f=440:d=8",
               "-shortest", "-pix_fmt", "yuv420p")


def _project(clip, ranges=((0, 2), (2, 4), (4, 6))):
    pr = P.Project()
    src = pr.add_source(media.probe(clip))
    for a, b in ranges:
        pr.add_segment(src.id, a, b).framing.mode = "crop"
    return pr


# ------------------------------------------------------------------ timeline maths
def test_snap_edges_and_drop_index(clip):
    pr = _project(clip)
    assert timeline.edges(pr.segments) == [0.0, 2.0, 4.0, 6.0]
    assert timeline.edges(pr.segments, skip=1) == [0.0, 2.0, 4.0, 6.0]   # the neighbours' edges stay
    assert timeline.snap(3.93, [0, 2, 4], 0.1) == (4, 4)
    assert timeline.snap(3.5, [0, 2, 4], 0.1) == (3.5, None)
    assert timeline.drop_index(pr.segments, 0, 5.5) == 2       # dragged to the end
    assert timeline.drop_index(pr.segments, 2, 0.2) == 0       # dragged to the front
    assert timeline.drop_index(pr.segments, 1, 2.9) == 1       # back where it was


def test_view_zoom_keeps_the_point_under_the_mouse():
    v = timeline.View()
    v.zoom_at(4, around=30, total=60)
    assert v.zoom == 4 and v.span(60) == 15
    assert v.x_of(30, 60, 100, 600) == pytest.approx(100 + (30 - v.start) / 15 * 600)
    assert v.t_of(v.x_of(30, 60, 100, 600), 60, 100, 600) == pytest.approx(30)
    v.start = 100
    v.clamp(60)
    assert v.start == pytest.approx(45)          # can't scroll past the end
    v.zoom_at(0.01, 10, 60)
    assert v.zoom == 1 and v.start == 0


def test_trim_to_keeps_the_moment_inside_its_video(clip):
    pr = _project(clip)
    pr.trim_to(1, "in", 2.5)
    pr.trim_to(1, "out", 99)
    assert (pr.segments[1].source_in, pr.segments[1].source_out) == (2.5, pytest.approx(8.0, abs=0.05))
    pr.trim_to(1, "in", 50)
    assert pr.segments[1].source_in == pytest.approx(pr.segments[1].source_out - 0.1)
    with pytest.raises(ValueError):
        pr.trim_to(0, "middle", 1)


def test_trim_an_inserted_reaction_sets_its_length(clip, tmp_path):
    from PIL import Image
    from clipping.editor import reactions as R
    pic = tmp_path / "r.png"
    Image.new("RGB", (64, 64), (0, 0, 0)).save(pic)
    pr = _project(clip)
    seg = R.insert(pr, str(pic), 1.0, 2.0)
    pr.trim_to(pr.segment_index(seg.id), "out", 3.5)
    assert seg.length == 3.5
    pr.trim_to(pr.segment_index(seg.id), "out", 99)
    assert seg.length == R.SECONDS[1]


# ------------------------------------------------------------------ pop-ups follow their moment
def test_old_popups_are_anchored_where_they_are_and_then_follow(clip):
    pr = _project(clip)
    pr.overlays.append({"id": "pop_1", "type": "text", "start": 2.5, "end": 3.5, "text": "LOL", "position": "middle"})
    O.refresh(pr)
    pop = pr.overlays[0]
    assert (pop["start"], pop["end"]) == (2.5, 3.5)            # an old edit shows it at the same time
    pr.move_segment(1, 0)                                      # its moment goes first
    O.refresh(pr)
    assert (pop["start"], pop["end"]) == (0.5, 1.5)
    pr.trim_to(0, "in", 2.2)                                    # trim the front of its moment
    O.refresh(pr)
    assert pop["start"] == pytest.approx(0.3)
    pr.remove_segment(0)
    O.refresh(pr)
    assert pop["visible"] is False and T.popups(pr) == []      # hidden with its moment; undo brings it back


# ------------------------------------------------------------------ placing
def test_named_spots_render_exactly_as_before():
    assert T.hook_effect("pop", 540, 960) == "\\fscx70\\fscy70\\t(0,160,\\fscx100\\fscy100)"
    assert T.hook_effect("slide", 540, 960) == "\\an8\\move(270,-96,270,115,0,260)"
    assert T.hook_effect("bounce", 540, 960).startswith("\\fscx40\\fscy40")
    pr = P.Project()
    pr.overlays = [{"type": "text", "start": 0, "end": 1, "text": "hey", "position": "middle"}]
    assert "\\an5\\pos(248,403)\\fscx70\\fscy70\\t(0,120,\\fscx100\\fscy100)" in T.build_ass(pr, 540, 960)


def test_drag_and_resize_every_kind(clip, tmp_path):
    from PIL import Image
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (200, 100), (255, 0, 0, 255)).save(logo)
    pr = _project(clip)
    pr.hook = {"text": "Big news", "seconds": 3}
    pr.logo = {"path": str(logo), "corner": "top-left", "size": 0.16}
    pr.overlays.append({"id": "pop_1", "type": "text", "start": 0.5, "end": 2, "text": "LOL", "position": "middle"})
    O.add_mark(pr, "arrow", 1.0, x=0.5, y=0.5)
    O.add_reaction(pr, str(logo), 1.0, "corner", 1.5, "top-left")
    O.refresh(pr)
    w, h = 540, 960
    items = {it.kind: it for it in PL.items_at(pr, 1.2, w, h)}
    assert set(items) == {"hook", "popup", "mark", "corner", "logo"}

    PL.move(pr, items["hook"], 0, 300, w, h)
    assert T.hook_place(pr.hook) == (0.5, pytest.approx((items["hook"].y + 300) / h, abs=1e-3))
    assert "\\an8\\pos(270," in T.build_ass(pr, w, h)
    PL.resize(pr, items["hook"], items["hook"].w * 1.5, w, h)
    assert pr.hook["scale"] == pytest.approx(1.5)
    assert "\\fscx105\\fscy105" in T.build_ass(pr, w, h)        # 70 % pop start, made 1.5x bigger

    PL.move(pr, items["popup"], -100, -200, w, h)
    pop = PL.find(pr, "pop_1")
    assert T.popup_xy(pop) == (pytest.approx((items["popup"].x - 100 + items["popup"].w / 2) / w, abs=1e-3),
                               pytest.approx((items["popup"].y - 200 + items["popup"].h / 2) / h, abs=1e-3))

    PL.move(pr, items["mark"], 54, 96, w, h)
    mark = PL.find(pr, items["mark"].key)
    assert (mark["x"], mark["y"]) == (pytest.approx(0.6, abs=0.01), pytest.approx(0.6, abs=0.01))
    PL.resize(pr, items["mark"], 999, w, h)
    assert mark["size"] == PL.MAX_SIZE["mark"]

    PL.move(pr, items["corner"], 200, 300, w, h)
    react = PL.find(pr, items["corner"].key)
    x, y, bw, bh = O.reaction_rect(react, w, h)
    assert (x, y) == ((items["corner"].x + 200) // 2 * 2, (items["corner"].y + 300) // 2 * 2) and bw == items["corner"].w
    PL.resize(pr, items["corner"], 270, w, h)
    assert O.reaction_rect(react, w, h)[2] == 270

    PL.move(pr, items["logo"], 100, 0, w, h)
    assert O.logo_rect(pr.logo, w, h, (200, 100))[0] == items["logo"].x + 100
    PL.reset(pr, items["logo"])
    assert "x" not in pr.logo and O.logo_rect(pr.logo, w, h, (200, 100))[0] == items["logo"].x


# ------------------------------------------------------------------ faster previews
def _pixel(path, t):
    out = subprocess.run(["ffmpeg", "-loglevel", "error", "-ss", str(t), "-i", path, "-frames:v", "1",
                          "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True)
    return tuple(out.stdout[:3])


def test_preview_only_remakes_what_changed_and_matches_one_pass(clip, tmp_path):
    pr = _project(clip)
    pr.hook = {"text": "hi", "seconds": 1}
    folder = str(tmp_path / "edit")
    t1 = {}
    out = render.make_preview(pr, folder, timings=t1)
    assert t1["pieces_made"] == 3 and media.probe(out).duration == pytest.approx(6.0, abs=0.1)
    # the same picture and length as the old single-pass preview
    single = str(tmp_path / "single.mp4")
    with __import__("tempfile").TemporaryDirectory() as work:
        render.run(render.build_command(pr, single, render.PREVIEW, render.text_layers_for(pr, render.PREVIEW, work),
                                        None, []), pr.total_length)
    for t in (1.5, 3.0, 5.0):
        a, b = _pixel(out, t), _pixel(single, t)
        assert max(abs(x - y) for x, y in zip(a, b)) < 12, (t, a, b)

    pr.hook["text"] = "hello"
    t2 = {}
    render.make_preview(pr, folder, timings=t2)
    assert t2["pieces_made"] == 0                               # text only: no moment is made again
    pr.trim_to(1, "out", 3.5)
    t3 = {}
    out = render.make_preview(pr, folder, timings=t3)
    assert t3["pieces_made"] == 1 and media.probe(out).duration == pytest.approx(5.5, abs=0.1)
    pr.move_segment(2, 0)
    t4 = {}
    out = render.make_preview(pr, folder, timings=t4)
    assert t4["pieces_made"] == 0
    assert _pixel(out, 0.5)[2] > 200                            # the blue moment now comes first


def test_export_is_still_one_pass_from_the_videos(clip):
    pr = _project(clip)
    cmd = render.build_command(pr, "o.mp4")
    assert cmd.count(clip) == 3 and "concat=n=3" in " ".join(cmd)


def test_clean_frame_is_the_framed_moment(clip, tmp_path):
    pr = _project(clip)
    png = render.clean_frame(pr, 4.5, str(tmp_path / "f.png"))
    from PIL import Image
    with Image.open(png) as im:
        assert im.size == (540, 960) and im.convert("RGB").getpixel((270, 480))[2] > 200


# ------------------------------------------------------------------ in the window
class _Ev:
    def __init__(self, x, y, state=0, x_root=0, y_root=0):
        self.x, self.y, self.state, self.x_root, self.y_root = x, y, state, x_root, y_root


def _moment_y():
    from clipping.editor.gui.timeline_panel import LANES, LANE_H, LANE_TOP
    return LANE_TOP + LANES.index("Moments") * (LANE_H + 2) + LANE_H / 2


def test_drag_trim_move_and_zoom_in_the_window(app):  # noqa: F811
    for a, b in ((0, 3), (4, 7), (8, 11)):
        app.project.add_segment(app.src.id, a, b)
    app.commit()
    app.root.update()
    y = _moment_y()
    undo_steps = len(app.history._undo)
    # drag the end of moment 1 to the left by about 1 s (snapping off)
    x1 = app.tl_x(3.0)
    app._tl_press(_Ev(x1 - 1, y))
    app._tl_motion(_Ev(app.tl_x(2.0), y, state=0x0001))
    app._tl_release(_Ev(app.tl_x(2.0), y, state=0x0001))
    assert app.project.segments[0].source_out == pytest.approx(2.0, abs=0.15)
    assert len(app.history._undo) == undo_steps + 1                # one drag = one undo step
    app.undo()
    assert app.project.segments[0].source_out == 3.0
    # a drag that comes close to the next moment's edge snaps onto it
    app._tl_press(_Ev(app.tl_x(3.0) - 1, y))
    app._tl_motion(_Ev(app.tl_x(6.0) + 3, y))
    app._tl_release(_Ev(app.tl_x(6.0) + 3, y))
    assert app.project.segments[0].source_out == pytest.approx(6.0, abs=0.001)
    app.undo()
    # move moment 3 to the front
    first = app.project.segments[2].id
    app._tl_press(_Ev(app.tl_x(7.5), y))
    app._tl_motion(_Ev(app.tl_x(0.3), y))
    app._tl_release(_Ev(app.tl_x(0.3), y))
    assert app.project.segments[0].id == first
    # Esc cancels a drag
    app._tl_press(_Ev(app.tl_x(1.5), y))
    app._tl_motion(_Ev(app.tl_x(8.0), y))
    app._tl_cancel()
    app._tl_release(_Ev(app.tl_x(8.0), y))
    assert app.project.segments[0].id == first
    # zoom in, then Fit
    app.zoom_timeline(4, around=4.5)
    assert app.tl_view.zoom == 4 and app.tl_x(4.5) == pytest.approx(app.tl_view.x_of(4.5, 9, *app._tl_area()))
    app.fit_timeline()
    assert app.tl_view.zoom == 1
    # a click without dragging still selects the moment
    app._tl_press(_Ev(app.tl_x(4.5), y))
    app._tl_release(_Ev(app.tl_x(4.5), y))
    assert app.tree.selection()[0] == app.project.segments[1].id


def test_place_mode_moves_the_hook_and_undo_works(app, tmp_path):  # noqa: F811
    from PIL import Image
    app.project.add_segment(app.src.id, 0, 6)
    app.project.hook = {"text": "Wait for it", "seconds": 3}
    app.commit()
    frame = tmp_path / "frame.png"
    Image.new("RGB", (540, 960), (20, 20, 20)).save(frame)
    app._place_dir = str(tmp_path)
    app._show_place(str(frame), 0.5)
    assert app.placing and not app.player.widget.winfo_ismapped()
    hook = next(it for it in app._pl["items"] if it.kind == "hook")
    ox, oy = app._pl["ox"], app._pl["oy"]
    cx, cy = ox + hook.x + hook.w / 2, oy + hook.y + hook.h / 2
    app._pl_press(_Ev(cx, cy))
    app._pl_motion(_Ev(cx, cy + 100))
    app._pl_release(_Ev(cx, cy + 100))
    assert app.project.hook["y"] == pytest.approx((hook.y + 100) / app._pl["h"], abs=0.01)
    app.undo()
    assert "y" not in app.project.hook
    app.leave_place()
    assert not app.placing


def test_drop_a_reaction_on_the_timeline(app, tmp_path):  # noqa: F811
    from PIL import Image
    pic = tmp_path / "r.png"
    Image.new("RGB", (64, 64), (0, 0, 0)).save(pic)
    app.project.add_segment(app.src.id, 0, 6)
    app.commit()
    app.root.update()
    c = app.timeline
    t_root = app.timeline_time_at_root(c.winfo_rootx() + int(app.tl_x(2.0)), c.winfo_rooty() + 30)
    assert t_root == pytest.approx(2.0, abs=0.1)
    assert app.timeline_time_at_root(c.winfo_rootx() + 5, c.winfo_rooty() + 30) is None   # over the lane names
    app._rdrag = {"path": str(pic), "x0": 0, "y0": 0, "ghost": None}
    app._react_drag_motion(_Ev(0, 0, x_root=c.winfo_rootx() + int(app.tl_x(2.0)), y_root=c.winfo_rooty() + 30))
    assert app._rdrag["ghost"] is not None
    app.react_style.set("insert")
    app._react_drag_drop(_Ev(0, 0, x_root=c.winfo_rootx() + int(app.tl_x(2.0)), y_root=c.winfo_rooty() + 30))
    assert [s.reaction for s in app.project.segments] == [False, True, False]
    assert os.path.basename(app.project.source(app.project.segments[1].source_id).path) == "r.png"
