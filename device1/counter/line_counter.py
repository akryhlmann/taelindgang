import logging
from typing import Dict, List, Optional, Set, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


def _cross_product_sign(
    line_p1: Tuple[int, int],
    line_p2: Tuple[int, int],
    point: Tuple[int, int],
) -> float:
    """Returns the sign of the cross product (line_p2-line_p1) x (point-line_p1).
    Positive means point is on the left side of the directed line p1->p2.
    """
    dx = line_p2[0] - line_p1[0]
    dy = line_p2[1] - line_p1[1]
    px = point[0] - line_p1[0]
    py = point[1] - line_p1[1]
    return dx * py - dy * px


def _segments_intersect(
    a1: Tuple[int, int],
    a2: Tuple[int, int],
    b1: Tuple[int, int],
    b2: Tuple[int, int],
) -> bool:
    """Check if segment a1->a2 intersects segment b1->b2."""
    d1 = _cross_product_sign(b1, b2, a1)
    d2 = _cross_product_sign(b1, b2, a2)
    d3 = _cross_product_sign(a1, a2, b1)
    d4 = _cross_product_sign(a1, a2, b2)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
        (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
    ):
        return True
    return False


class LineCounter:
    def __init__(
        self,
        point1: List[float],
        point2: List[float],
        in_direction: str,
        frame_width: int,
        frame_height: int,
    ):
        self._frame_w = frame_width
        self._frame_h = frame_height
        self._in_direction = in_direction.lower()
        self._p1 = self._to_pixels(point1)
        self._p2 = self._to_pixels(point2)
        self._total_in = 0
        self._total_out = 0
        # Maps object_id -> side of the line (-1 or 1) at last seen position
        self._object_sides: Dict[int, float] = {}
        # Set of object_ids that have already been counted crossing in/out
        self._counted_ids: Set[int] = set()

    def _to_pixels(self, normalized: List[float]) -> Tuple[int, int]:
        return (int(normalized[0] * self._frame_w), int(normalized[1] * self._frame_h))

    def _side(self, point: Tuple[int, int]) -> float:
        return _cross_product_sign(self._p1, self._p2, point)

    def _is_in_direction(self, prev_side: float, curr_side: float) -> bool:
        """Determine if the crossing from prev_side to curr_side corresponds to 'in'."""
        d = self._in_direction
        # The line goes from p1 to p2.
        # Cross product sign conventions:
        #  positive cross -> point is to the left of directed p1->p2
        #  negative cross -> point is to the right
        # "top"    -> moving from top of image to bottom means curr_side becomes negative
        #             (since for a horizontal line left->right, top is positive cross)
        # We define which sign change means "in" based on direction label.
        if d == "top":
            # "in" means entering from the top, i.e. moving downward through the line
            # downward: y increases, so crossing from positive (above) to negative (below)
            return prev_side > 0 and curr_side < 0
        elif d == "bottom":
            return prev_side < 0 and curr_side > 0
        elif d == "left":
            # "in" means entering from the left, crossing rightward
            return prev_side > 0 and curr_side < 0
        elif d == "right":
            return prev_side < 0 and curr_side > 0
        return prev_side > 0 and curr_side < 0

    def process_tracks(self, tracks: dict, prev_tracks: dict) -> Tuple[int, int]:
        new_ins = 0
        new_outs = 0

        current_ids = set(tracks.keys())

        # Remove side tracking for objects no longer present
        for oid in list(self._object_sides.keys()):
            if oid not in current_ids:
                del self._object_sides[oid]

        for obj_id, data in tracks.items():
            centroid = data["centroid"]
            curr_side = self._side(centroid)

            if obj_id not in prev_tracks:
                # New object, just record its side
                self._object_sides[obj_id] = curr_side
                continue

            prev_side = self._object_sides.get(obj_id)
            if prev_side is None:
                self._object_sides[obj_id] = curr_side
                continue

            prev_centroid = prev_tracks[obj_id]["centroid"]

            if prev_side * curr_side < 0:
                # Check segment intersection with counting line for accuracy
                if _segments_intersect(prev_centroid, centroid, self._p1, self._p2):
                    if obj_id not in self._counted_ids:
                        self._counted_ids.add(obj_id)
                        if self._is_in_direction(prev_side, curr_side):
                            new_ins += 1
                            self._total_in += 1
                            logger.debug("Object %d counted IN (total_in=%d)", obj_id, self._total_in)
                        else:
                            new_outs += 1
                            self._total_out += 1
                            logger.debug("Object %d counted OUT (total_out=%d)", obj_id, self._total_out)

            self._object_sides[obj_id] = curr_side

        return new_ins, new_outs

    def get_totals(self) -> Tuple[int, int]:
        return self._total_in, self._total_out

    def reset(self) -> None:
        self._total_in = 0
        self._total_out = 0
        self._object_sides.clear()
        self._counted_ids.clear()
        logger.info("LineCounter reset")

    def draw(self, frame: np.ndarray) -> np.ndarray:
        out = frame.copy()
        cv2.line(out, self._p1, self._p2, (0, 255, 255), 2)
        cv2.circle(out, self._p1, 5, (0, 200, 255), -1)
        cv2.circle(out, self._p2, 5, (0, 200, 255), -1)

        # Draw direction arrow at midpoint
        mid_x = (self._p1[0] + self._p2[0]) // 2
        mid_y = (self._p1[1] + self._p2[1]) // 2
        arrow_offsets = {
            "top": (0, -30),
            "bottom": (0, 30),
            "left": (-30, 0),
            "right": (30, 0),
        }
        dx, dy = arrow_offsets.get(self._in_direction, (0, -30))
        cv2.arrowedLine(
            out,
            (mid_x, mid_y),
            (mid_x + dx, mid_y + dy),
            (0, 255, 0),
            2,
            tipLength=0.4,
        )

        label_in = f"IN: {self._total_in}"
        label_out = f"OUT: {self._total_out}"
        cv2.putText(out, label_in, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
        cv2.putText(out, label_out, (10, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)

        return out
