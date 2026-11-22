"""
clipping.editor.framing — Getting a wide video into a 9:16 frame.

crop_box()   pure maths: which part of the source to keep.
auto_centre() looks at a few pictures of a moment and finds where the people
              are, so the crop follows the action. It uses OpenCV (already a
              dependency) and is cheap on a CPU: a handful of small frames per
              moment, worked out once and saved in the project.
"""

from __future__ import annotations

import os

OUT_W, OUT_H = 1080, 1920


def _even(n: float) -> int:
    n = int(round(n))
    return n - (n % 2)


def crop_box(src_w: int, src_h: int, centre: tuple[float, float] = (0.5, 0.5),
             zoom: float = 1.0, out_w: int = OUT_W, out_h: int = OUT_H) -> tuple[int, int, int, int]:
    """The (width, height, x, y) of the area to cut out of the source so that,
    once scaled, it fills ``out_w`` x ``out_h`` without stretching."""
    if src_w <= 0 or src_h <= 0:
        raise ValueError("Unknown video size.")
    zoom = max(1.0, float(zoom or 1.0))
    target = out_w / out_h
    if src_w / src_h > target:          # wider than 9:16: keep full height
        ch = src_h
        cw = ch * target
    else:                                # taller/narrower: keep full width
        cw = src_w
        ch = cw / target
    cw, ch = _even(max(2, cw / zoom)), _even(max(2, ch / zoom))
    cx, cy = centre
    x = min(max(0.0, cx * src_w - cw / 2), src_w - cw)
    y = min(max(0.0, cy * src_h - ch / 2), src_h - ch)
    return cw, ch, _even(x), _even(y)


def auto_centre(path: str, start: float, end: float, samples: int = 6) -> tuple[float, float] | None:
    """Where the faces are in this moment, as fractions (x, y) of the frame.
    Returns None when no faces are found (the crop then stays in the middle)."""
    try:
        import cv2
    except ImportError:
        return None
    cascade_file = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
    detector = cv2.CascadeClassifier(cascade_file)
    if detector.empty():
        return None
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return None
    xs, ys, weights = [], [], []
    try:
        span = max(0.1, end - start)
        for i in range(samples):
            t = start + span * (i + 0.5) / samples
            cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            h, w = frame.shape[:2]
            scale = 360 / h if h > 360 else 1.0
            small = cv2.resize(frame, (int(w * scale), int(h * scale))) if scale != 1.0 else frame
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
            faces = detector.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=5, minSize=(24, 24))
            sh, sw = gray.shape[:2]
            for (fx, fy, fw, fh) in faces:
                area = float(fw * fh)
                xs.append((fx + fw / 2) / sw * area)
                ys.append((fy + fh / 2) / sh * area)
                weights.append(area)
    finally:
        cap.release()
    if not weights:
        return None
    total = sum(weights)
    return round(sum(xs) / total, 3), round(sum(ys) / total, 3)
