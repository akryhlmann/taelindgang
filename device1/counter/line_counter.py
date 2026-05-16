import logging
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class LineCounter:
    def __init__(
        self,
        point1: List[float],
        point2: List[float],
        in_direction: str,
        frame_width: int,
        frame_height: int,
    ):
        self._in_direction = in_direction.lower()
        self._frame_width = frame_width
        self._frame_height = frame_height

        self._px1 = (
            int(point1[0] * frame_width),
            int(point1[1] * frame_height),
        )
        self._px2 = (
            int(point2[0] * frame_width),
            int(point2[1] * frame_height),
        )

        self._total_in = 0
        self._total_out = 0
        self._counted_ids: Set[int] = set()
        self._prev_sides: Dict[int, int] = {}

    def _side_of_line(self, point: Tuple[int, int]) -> int:
        ax, ay = self._px1
        bx, by = self._px2
        px, py = point
        cross = (bx - ax) * (py - ay) - (by - ay) * (px - ax)
        if cross > 0:
            return 1
        elif cross < 0:
            return -1
        return 0

    def _crossing_is_in(self, from_side: int, to_side: int) -> Optional[bool]:
        if from_side == to_side or from_side == 0 or to_side == 0:
            return None

        dx = self._px2[0] - self._px1[0]
        dy = self._px2[1] - self._px1[1]

        if self._in_direction == "top":
            return to_side == -1
        elif self._in_direction == "bottom":
            return to_side == 1
        elif self._in_direction == "left":
            return to_side == -1
        elif self._in_direction == "right":
            return to_side == 1
        return None

    def process_tracks(
        self, tracks: dict, prev_tracks: dict
    ) -> Tuple[int, int]:
        new_ins = 0
        new_outs = 0

        for obj_id, track_data in tracks.items():
            centroid = track_data["centroid"]
            current_side = self._side_of_line(centroid)

            if obj_id not in self._prev_sides:
                self._prev_sides[obj_id] = current_side
                continue

            prev_side = self._prev_sides[obj_id]
            self._prev_sides[obj_id] = current_side

            if prev_side == current_side or current_side == 0:
                continue

            if obj_id in self._counted_ids:
                continue

            is_in = self._crossing_is_in(prev_side, current_side)
            if is_in is None:
                continue

            self._counted_ids.add(obj_id)

            if is_in:
                self._total_in += 1
                new_ins += 1
                logger.debug("Object %d crossed IN (total_in=%d)", obj_id, self._total_in)
            else:
                self._total_out += 1
                new_outs += 1
                logger.debug("Object %d crossed OUT (total_out=%d)", obj_id, self._total_out)

        departed_ids = set(self._prev_sides.keys()) - set(tracks.keys())
        for obj_id in departed_ids:
            del self._prev_sides[obj_id]
            self._counted_ids.discard(obj_id)

        return new_ins, new_outs

    def get_totals(self) -> Tuple[int, int]:
        return self._total_in, self._total_out

    def reset(self) -> None:
        self._total_in = 0
        self._total_out = 0
        self._counted_ids.clear()
        self._prev_sides.clear()
        logger.info("LineCounter reset")

    def draw(self, frame: np.ndarray) -> np.ndarray:
        import cv2

        output = frame.copy()
        cv2.line(output, self._px1, self._px2, (0, 255, 255), 2)

        mid_x = (self._px1[0] + self._px2[0]) // 2
        mid_y = (self._px1[1] + self._px2[1]) // 2

        arrow_offsets = {
            "top": (0, -30),
            "bottom": (0, 30),
            "left": (-30, 0),
            "right": (30, 0),
        }
        offset = arrow_offsets.get(self._in_direction, (0, -30))
        arrow_end = (mid_x + offset[0], mid_y + offset[1])
        cv2.arrowedLine(output, (mid_x, mid_y), arrow_end, (0, 255, 0), 2)

        cv2.putText(
            output,
            f"IN: {self._total_in}",
            (10, 30),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 255, 0),
            2,
        )
        cv2.putText(
            output,
            f"OUT: {self._total_out}",
            (10, 65),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (0, 0, 255),
            2,
        )

        return output
