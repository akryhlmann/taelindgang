import logging
import math
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CentroidTracker:
    def __init__(self, max_disappeared: int = 10, max_distance: float = 100.0):
        self._max_disappeared = max_disappeared
        self._max_distance = max_distance
        self._next_id = 0
        # {object_id: {"centroid": (x,y), "bbox": (x1,y1,x2,y2), "disappeared": int}}
        self._objects: Dict[int, dict] = {}

    def _euclidean(self, a: Tuple[int, int], b: Tuple[int, int]) -> float:
        return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)

    def update(self, detections: List[dict]) -> OrderedDict:
        if not detections:
            for obj_id in list(self._objects):
                self._objects[obj_id]["disappeared"] += 1
                if self._objects[obj_id]["disappeared"] > self._max_disappeared:
                    del self._objects[obj_id]
            return self.get_tracks()

        input_centroids = [d["centroid"] for d in detections]
        input_bboxes = [d.get("bbox", (0, 0, 0, 0)) for d in detections]

        if not self._objects:
            for centroid, bbox in zip(input_centroids, input_bboxes):
                self._register(centroid, bbox)
            return self.get_tracks()

        object_ids = list(self._objects.keys())
        object_centroids = [self._objects[oid]["centroid"] for oid in object_ids]

        # Build cost matrix (rows=existing objects, cols=new detections)
        rows = len(object_centroids)
        cols = len(input_centroids)
        cost = [
            [self._euclidean(object_centroids[r], input_centroids[c]) for c in range(cols)]
            for r in range(rows)
        ]

        # Greedy assignment sorted by minimum distance
        used_rows = set()
        used_cols = set()
        pairs = sorted(
            [(cost[r][c], r, c) for r in range(rows) for c in range(cols)],
            key=lambda x: x[0],
        )
        for dist, row, col in pairs:
            if row in used_rows or col in used_cols:
                continue
            if dist > self._max_distance:
                break
            obj_id = object_ids[row]
            self._objects[obj_id]["centroid"] = input_centroids[col]
            self._objects[obj_id]["bbox"] = input_bboxes[col]
            self._objects[obj_id]["disappeared"] = 0
            used_rows.add(row)
            used_cols.add(col)

        unmatched_rows = set(range(rows)) - used_rows
        unmatched_cols = set(range(cols)) - used_cols

        for row in unmatched_rows:
            obj_id = object_ids[row]
            self._objects[obj_id]["disappeared"] += 1
            if self._objects[obj_id]["disappeared"] > self._max_disappeared:
                del self._objects[obj_id]

        for col in unmatched_cols:
            self._register(input_centroids[col], input_bboxes[col])

        return self.get_tracks()

    def _register(self, centroid: Tuple[int, int], bbox: Tuple[int, int, int, int]) -> None:
        self._objects[self._next_id] = {
            "centroid": centroid,
            "bbox": bbox,
            "disappeared": 0,
        }
        self._next_id += 1

    def get_tracks(self) -> OrderedDict:
        result = OrderedDict()
        for obj_id, data in self._objects.items():
            result[obj_id] = data
        return result
