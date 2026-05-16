import logging
import math
from collections import OrderedDict
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CentroidTracker:
    def __init__(self, max_disappeared: int = 10, max_distance: int = 100):
        self._max_disappeared = max_disappeared
        self._max_distance = max_distance

        self._next_object_id = 0
        self._objects: OrderedDict[int, dict] = OrderedDict()

    def _euclidean(self, a: Tuple[int, int], b: Tuple[int, int]) -> float:
        return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)

    def _register(self, centroid: Tuple[int, int], bbox: Tuple[int, int, int, int]) -> int:
        object_id = self._next_object_id
        self._objects[object_id] = {
            "centroid": centroid,
            "bbox": bbox,
            "disappeared": 0,
            "was_new": True,
        }
        self._next_object_id += 1
        logger.debug("Registered new object %d at %s", object_id, centroid)
        return object_id

    def _deregister(self, object_id: int) -> None:
        logger.debug("Deregistered object %d", object_id)
        del self._objects[object_id]

    def update(self, detections: List[dict]) -> OrderedDict:
        for obj_id in self._objects:
            self._objects[obj_id]["was_new"] = False

        if not detections:
            for obj_id in list(self._objects.keys()):
                self._objects[obj_id]["disappeared"] += 1
                if self._objects[obj_id]["disappeared"] > self._max_disappeared:
                    self._deregister(obj_id)
            return self.get_tracks()

        input_centroids = [d["centroid"] for d in detections]
        input_bboxes = [d["bbox"] for d in detections]

        if not self._objects:
            for centroid, bbox in zip(input_centroids, input_bboxes):
                self._register(centroid, bbox)
            return self.get_tracks()

        object_ids = list(self._objects.keys())
        object_centroids = [self._objects[oid]["centroid"] for oid in object_ids]

        distance_matrix = [
            [self._euclidean(oc, ic) for ic in input_centroids]
            for oc in object_centroids
        ]

        used_rows = set()
        used_cols = set()
        matched_pairs = []

        flat_distances = sorted(
            [
                (distance_matrix[r][c], r, c)
                for r in range(len(object_centroids))
                for c in range(len(input_centroids))
            ]
        )

        for dist, row, col in flat_distances:
            if row in used_rows or col in used_cols:
                continue
            if dist > self._max_distance:
                break
            matched_pairs.append((row, col))
            used_rows.add(row)
            used_cols.add(col)

        for row, col in matched_pairs:
            obj_id = object_ids[row]
            self._objects[obj_id]["centroid"] = input_centroids[col]
            self._objects[obj_id]["bbox"] = input_bboxes[col]
            self._objects[obj_id]["disappeared"] = 0

        unmatched_rows = set(range(len(object_centroids))) - used_rows
        unmatched_cols = set(range(len(input_centroids))) - used_cols

        for row in unmatched_rows:
            obj_id = object_ids[row]
            self._objects[obj_id]["disappeared"] += 1
            if self._objects[obj_id]["disappeared"] > self._max_disappeared:
                self._deregister(obj_id)

        for col in unmatched_cols:
            self._register(input_centroids[col], input_bboxes[col])

        return self.get_tracks()

    def get_tracks(self) -> OrderedDict:
        return OrderedDict(
            (oid, {
                "centroid": data["centroid"],
                "bbox": data["bbox"],
                "was_new": data["was_new"],
                "disappeared": data["disappeared"],
            })
            for oid, data in self._objects.items()
        )
