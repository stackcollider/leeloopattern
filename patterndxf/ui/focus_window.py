"""Focus: the step of Commissioning that sets the lens by hand (docs/DECISIONS.md 2026-11-19), transferred from tools/focus_test3.py.

The way it is used: lay the work sheet (50x70: marker 5 top left) so that its blue cross covers the red cross of the frame,
then turn the focus ring while looking at the four corner tiles: the edge width of the corner markers, px (smaller = sharper),
and the best value since Start new focus adjustment.

Finish opens when the whole sheet is in view, its centre is on the cross, it is turned not more than 10 deg and the four corners
are measured; the sharpness is judged by the person (there is no threshold yet): the worst corner goes to the hub and the line
for the journal to the log. Cancel ends the dialog with Rejected and no result.
Built on SheetScreen (patterndxf/ui/sheet_screen.py: the camera, the markers, the crosses, the place of the sheet, Resolution,
Snap, Fit, Cancel, Finish); here only the corner tiles and the result.
"""
import logging

import cv2
import numpy as np
from PySide6.QtWidgets import QLabel, QPushButton

from patterndxf.sheet import focus
from patterndxf.ui import theme
from patterndxf.ui.sheet_screen import SheetScreen, hex_bgr

CROP_SIDES, TILE_PX = 2.0, 190                  # a corner tile shows this many marker sides around the marker centre; its size, px
EDGE_GOOD_PX, EDGE_OK_PX = 2.0, 3.5             # the colour of a tile: green up to here, yellow up to here, red above (a first guess)


class FocusDialog(SheetScreen):
    TITLE = "Focus"
    HINT = ("Lay the work sheet with the blue cross on the red one (50x70: marker 5 top left), "
            "then turn the focus ring by the corner tiles.")

    def __init__(self, profile, snap_folder=None, parent=None):
        super().__init__(profile, snap_folder, parent)
        self.tile_now, self.tile_best = {}, {}           # per corner: the edge width of the last picture and the best since the start
        self.reset_btn = QPushButton("Start new focus adjustment")
        self.reset_btn.setMinimumHeight(44)
        reset_hint = QLabel("Press after moving the sheet, the light or the lens.")
        reset_hint.setWordWrap(True)
        reset_hint.setStyleSheet(f"color: {theme.TEXT2}; font-size: 12px")
        self.actions.addWidget(self.reset_btn)
        self.actions.addWidget(reset_hint)
        self.reset_btn.clicked.connect(self.reset_best)
        self.begin()

    # ----- what SheetScreen asks
    def can_finish(self):
        return len(self.tile_now) == 4                   # the four corners are measured

    def on_mode_changed(self):
        self.tile_now, self.tile_best = {}, {}

    def draw_extra(self, out, to_view):
        self.draw_tiles(out)                             # the corner tiles over everything

    def finish(self):
        """The end of the step: the worst corner goes to the hub (Accepted), the line goes to the log."""
        corners = dict(self.tile_now)
        worst = max(corners.values())
        p = focus.place(self.pose, *self.frame_size)
        line = (f"Focus: worst corner {worst:.1f} px; corners px " + ", ".join(f"{k} {v:.1f}" for k, v in corners.items())
                + f"; sheet {self.pose[0]} centre {p['dx']:+.0f}, {p['dy']:+.0f} px, turn {p['turn']:+.1f} deg; "
                f"mode {self.mode_box.currentText().split('  ')[0]}; camera {self.profile.camera_text}")
        logging.info("%s", line)
        self.result = {"worst": worst, "corners": corners, "journal": line}
        self.accept()

    # ----- the corner tiles: the four extreme markers, enlarged, with the width of their edges
    def reset_best(self):
        self.tile_best = {}
        logging.info("Focus: start new focus adjustment")
        self.picture.render()

    def draw_tiles(self, out):
        """Four tiles in the corners of the picture: the surroundings of the marker nearest to that corner of the frame, enlarged,
        with the edge width in px (colour by EDGE_GOOD_PX and EDGE_OK_PX) and the best value since Start new focus adjustment."""
        bgr = self.last_bgr
        if bgr is None or not self.found:
            return
        fh, fw = bgr.shape[:2]
        vh, vw = out.shape[:2]
        tile = int(min(TILE_PX, vw / 5, vh / 4))
        if tile < 60:
            return
        head, foot, gap = 20, 26, 8
        total = head + tile + foot
        spots = {"top left": (gap, gap), "top right": (vw - tile - gap, gap),
                 "bottom right": (vw - tile - gap, vh - total - gap), "bottom left": (gap, vh - total - gap)}
        now = {}
        for corner, marker_id, (cx, cy), side in focus.pick_extremes(self.found, (fw, fh)):
            half = max(int(CROP_SIDES * side), 16)
            x0, y0 = int(min(max(cx - half, 0), fw - 2 * half)), int(min(max(cy - half, 0), fh - 2 * half))
            crop = bgr[y0:y0 + 2 * half, x0:x0 + 2 * half]
            if crop.shape[0] < 4 or crop.shape[1] < 4:
                continue
            value = focus.edge_px(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY))
            if np.isfinite(value):
                now[corner] = value
                self.tile_best[corner] = min(self.tile_best.get(corner, value), value)
            sx, sy = spots[corner]
            out[sy:sy + total, sx:sx + tile] = (33, 21, 21)                     # the dark ground of the tile
            out[sy + head:sy + head + tile, sx:sx + tile] = cv2.resize(crop, (tile, tile), interpolation=cv2.INTER_NEAREST)
            cv2.putText(out, f"{corner} (marker {marker_id})", (sx + 4, sy + 14), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (240, 245, 241), 1, cv2.LINE_AA)
            if np.isfinite(value):
                colour = hex_bgr(theme.OK if value <= EDGE_GOOD_PX else theme.WARN if value <= EDGE_OK_PX else theme.ERR)
                label = f"{value:.1f} px   best {self.tile_best[corner]:.1f}"
            else:
                colour, label = hex_bgr(theme.NEUTRAL), "no edges"
            out[sy + head + tile:sy + total, sx:sx + tile] = colour
            (tw, _), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
            cv2.putText(out, label, (sx + (tile - tw) // 2, sy + head + tile + 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
            cv2.rectangle(out, (sx, sy), (sx + tile - 1, sy + total - 1), (110, 110, 110), 1)
        had_four = len(self.tile_now) == 4
        self.tile_now = now
        if had_four != (len(now) == 4):                                # Finish needs the four corners measured
            self.refresh_finish()
