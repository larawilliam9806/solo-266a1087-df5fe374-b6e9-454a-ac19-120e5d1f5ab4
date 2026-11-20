import io
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pricing import layout  # noqa: E402
from pricing.csvparse import CsvError, parse_csv  # noqa: E402


def make_box(box_id, ref, text, x1, y1, x2, y2):
    return dict(id=box_id, box_ref=ref, text=text,
                x1=x1, y1=y1, x2=x2, y2=y2)


def sample_boxes():
    """Two rows, three columns.  Box 5 straddles two cells."""
    return [
        make_box(1, "1", "白砂糖", 10, 10, 90, 40),
        make_box(2, "2", "一级", 110, 10, 190, 40),
        make_box(3, "3", "5.00", 210, 10, 290, 40),
        make_box(4, "4", "红糖", 10, 110, 90, 140),
        make_box(5, "5", "跨格", 90, 10, 110, 40),
        make_box(6, "6", "出界", 400, 400, 450, 430),
    ]


H = [0, 100, 200]
V = [0, 100, 200, 300]


class GridTests(unittest.TestCase):
    def setUp(self):
        self.grid = layout.Grid(H, V)

    def test_grid_shape(self):
        self.assertEqual(self.grid.rows, 2)
        self.assertEqual(self.grid.cols, 3)
        self.assertTrue(self.grid.valid)

    def test_full_containment(self):
        self.assertEqual(self.grid.containing_cell((10, 10, 90, 40)), (0, 0))
        self.assertEqual(self.grid.containing_cell((110, 110, 190, 140)), (1, 1))
        self.assertIsNone(self.grid.containing_cell((90, 10, 110, 40)))

    def test_touching_boundary_is_inside(self):
        self.assertEqual(self.grid.containing_cell((0, 0, 100, 100)), (0, 0))

    def test_candidates_only_positive_overlap(self):
        candidates = [entry[2] for entry in self.grid.overlapping_cells((90, 10, 110, 40))]
        self.assertEqual(sorted(set(candidates)), [0, 1])

    def test_zero_area_box_is_not_contained(self):
        self.assertIsNone(self.grid.containing_cell((50, 50, 50, 50)))
        self.assertIsNone(self.grid.containing_cell((10, 10, 90, 10)))

    def test_out_of_bounds_has_no_candidates(self):
        self.assertEqual(self.grid.overlapping_cells((400, 400, 450, 430)), [])

    def test_float_noise_does_not_change_cell_key(self):
        a = layout.Grid([0, 100.0000001, 200], V)
        self.assertEqual(a.cell_key(0, 0), self.grid.cell_key(0, 0))


class EvaluateTests(unittest.TestCase):
    def setUp(self):
        self.grid = layout.Grid(H, V)

    def test_fully_contained_boxes_are_auto_assigned(self):
        evaluated = layout.evaluate(self.grid, sample_boxes()[:4], {})
        self.assertTrue(all(item["status"] == "assigned_auto" for item in evaluated))
        self.assertEqual([item["column"] for item in evaluated],
                         ["name", "spec", "price", "name"])

    def test_spanning_and_out_of_bounds_are_pending(self):
        evaluated = layout.evaluate(self.grid, sample_boxes(), {})
        by_id = {item["box_id"]: item for item in evaluated}
        self.assertEqual(by_id[5]["status"], "pending")
        self.assertEqual(by_id[6]["status"], "pending")
        self.assertEqual(layout.pending_count(evaluated), 2)

    def test_manual_decision_resolves_pending(self):
        decisions = {
            5: {"kind": "cell", "target_cell": self.grid.cell_key(0, 1),
                "overlap_cells": self.grid.overlapping_keys((90, 10, 110, 40))},
            6: {"kind": "exclude", "target_cell": "",
                "overlap_cells": []},
        }
        evaluated = layout.evaluate(self.grid, sample_boxes(), decisions)
        by_id = {item["box_id"]: item for item in evaluated}
        self.assertEqual(by_id[5]["status"], "assigned_manual")
        self.assertEqual(by_id[5]["column"], "spec")
        self.assertEqual(by_id[6]["status"], "excluded")
        self.assertEqual(layout.pending_count(evaluated), 0)

    def test_decision_is_invalidated_when_boundary_moves(self):
        decisions = {
            5: {"kind": "cell", "target_cell": self.grid.cell_key(0, 1),
                "overlap_cells": self.grid.overlapping_keys((90, 10, 110, 40))},
        }
        moved = layout.Grid(H, [0, 95, 200, 300])
        evaluated = layout.evaluate(moved, sample_boxes(), decisions)
        by_id = {item["box_id"]: item for item in evaluated}
        self.assertEqual(by_id[5]["status"], "stale")

    def test_decision_survives_unrelated_line_move(self):
        decisions = {
            5: {"kind": "cell", "target_cell": self.grid.cell_key(0, 1),
                "overlap_cells": self.grid.overlapping_keys((90, 10, 110, 40))},
        }
        # Move the bottom row line; box 5 lives in the top row and never
        # touches the line at y=100 or y=200 boundaries... except its cells
        # do touch y=100.  Move y=200 only, which is outside its cells.
        changed = layout.Grid([0, 100, 260], V)
        evaluated = layout.evaluate(changed, sample_boxes(), decisions)
        by_id = {item["box_id"]: item for item in evaluated}
        self.assertEqual(by_id[5]["status"], "assigned_manual")

    def test_decision_survives_new_line_outside_intersecting_cells(self):
        decisions = {
            5: {"kind": "cell", "target_cell": self.grid.cell_key(0, 1),
                "overlap_cells": self.grid.overlapping_keys((90, 10, 110, 40))},
        }
        # A new row line at y=180 only splits the second row's cells; the
        # cells intersecting box 5 keep their boundaries.
        added = layout.Grid([0, 100, 180, 200], V)
        evaluated = layout.evaluate(added, sample_boxes(), decisions)
        by_id = {item["box_id"]: item for item in evaluated}
        self.assertEqual(by_id[5]["status"], "assigned_manual")

    def test_decision_invalidated_when_line_crosses_intersecting_cell(self):
        decisions = {
            5: {"kind": "cell", "target_cell": self.grid.cell_key(0, 1),
                "overlap_cells": self.grid.overlapping_keys((90, 10, 110, 40))},
        }
        crossed = layout.Grid([0, 25, 100, 200], V)
        evaluated = layout.evaluate(crossed, sample_boxes(), decisions)
        by_id = {item["box_id"]: item for item in evaluated}
        self.assertEqual(by_id[5]["status"], "stale")

    def test_exclusion_survives_becoming_fully_contained(self):
        excluded = {"kind": "exclude", "target_cell": "",
                    "overlap_cells": self.grid.overlapping_keys((90, 10, 110, 40))}
        decisions = {5: excluded}
        # Shrink the box so it sits wholly in one cell: the intersecting cell
        # set changes, so the exclusion must be re-confirmed, never dropped.
        boxes = [make_box(5, "5", "跨格", 45, 10, 55, 40)]
        evaluated = layout.evaluate(self.grid, boxes, decisions)
        self.assertEqual(evaluated[0]["status"], "stale")

    def test_exclusion_still_applies_when_contained_and_unchanged(self):
        box = make_box(5, "5", "边注", 105, 10, 115, 40)
        excluded = {"kind": "exclude", "target_cell": "",
                    "overlap_cells": self.grid.overlapping_keys((105, 10, 115, 40))}
        evaluated = layout.evaluate(self.grid, [box], {5: excluded})
        self.assertEqual(evaluated[0]["status"], "excluded")

    def test_fully_contained_box_supersedes_cell_decision(self):
        box = make_box(5, "5", "边注", 10, 10, 90, 40)
        decision = {"kind": "cell", "target_cell": self.grid.cell_key(0, 1),
                    "overlap_cells": []}
        evaluated = layout.evaluate(self.grid, [box], {5: decision})
        self.assertEqual(evaluated[0]["status"], "assigned_auto")
        self.assertEqual(evaluated[0]["column"], "name")

    def test_decision_invalidated_when_chosen_cell_vanishes(self):
        decisions = {
            5: {"kind": "cell", "target_cell": self.grid.cell_key(0, 1),
                "overlap_cells": self.grid.overlapping_keys((90, 10, 110, 40))},
        }
        # Remove the y=100 line: box 5 then overlaps new cells only.
        changed = layout.Grid([0, 200], V)
        evaluated = layout.evaluate(changed, sample_boxes(), decisions)
        by_id = {item["box_id"]: item for item in evaluated}
        self.assertEqual(by_id[5]["status"], "stale")


class DecisionValidationTests(unittest.TestCase):
    def setUp(self):
        self.grid = layout.Grid(H, V)
        self.item = next(item for item in layout.evaluate(self.grid, sample_boxes(), {})
                         if item["box_id"] == 5)

    def test_only_overlapping_cell_allowed(self):
        with self.assertRaises(layout.LayoutError):
            layout.make_decision(self.grid, self.item, "cell", self.grid.cell_key(1, 2))

    def test_overlapping_cell_allowed(self):
        decision = layout.make_decision(self.grid, self.item, "cell",
                                        self.grid.cell_key(0, 0))
        self.assertEqual(decision["kind"], "cell")
        self.assertIn(self.grid.cell_key(0, 1), decision["overlap_cells"])

    def test_exclude_recorded_with_overlapping_cells(self):
        decision = layout.make_decision(self.grid, self.item, "exclude", None)
        self.assertEqual(decision["kind"], "exclude")
        self.assertEqual(decision["target_cell"], "")
        self.assertTrue(decision["overlap_cells"])

    def test_unknown_kind_rejected(self):
        with self.assertRaises(layout.LayoutError):
            layout.make_decision(self.grid, self.item, "wiggle", None)


class ReadingOrderTests(unittest.TestCase):
    def test_same_cell_top_to_bottom(self):
        grid = layout.Grid([0, 100], V)
        boxes = [
            make_box(1, "1", "下", 10, 60, 90, 90),
            make_box(2, "2", "上", 10, 10, 90, 40),
        ]
        evaluated = layout.evaluate(grid, boxes, {})
        rows = layout.build_rows(evaluated)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "上下")

    def test_same_height_left_to_right(self):
        grid = layout.Grid([0, 100], V)
        boxes = [
            make_box(1, "1", "右", 50, 10, 80, 40),
            make_box(2, "2", "左", 10, 12, 40, 42),
        ]
        evaluated = layout.evaluate(grid, boxes, {})
        rows = layout.build_rows(evaluated)
        self.assertEqual(rows[0]["name"], "左右")

    def test_rows_follow_grid_row_order(self):
        grid = layout.Grid(H, V)
        boxes = [make_box(1, "1", "第二行", 10, 110, 90, 140),
                 make_box(2, "2", "第一行", 10, 10, 90, 40)]
        rows = layout.build_rows(layout.evaluate(grid, boxes, {}))
        self.assertEqual([row["name"] for row in rows], ["第一行", "第二行"])

    def test_price_column_and_empty_columns(self):
        grid = layout.Grid(H, V)
        boxes = [make_box(1, "1", "盐", 10, 10, 90, 40),
                 make_box(2, "2", "1.2", 210, 10, 290, 40)]
        rows = layout.build_rows(layout.evaluate(grid, boxes, {}))
        self.assertEqual(rows[0]["name"], "盐")
        self.assertEqual(rows[0]["spec"], "")
        self.assertEqual(rows[0]["price"], "1.2")


class LayoutNormalisationTests(unittest.TestCase):
    def test_requires_four_vertical_lines(self):
        with self.assertRaises(layout.LayoutError):
            layout.normalize_layout([0, 100], [0, 100, 200])
        with self.assertRaises(layout.LayoutError):
            layout.normalize_layout([0, 100], [0, 100, 200, 300, 400])

    def test_requires_two_horizontal_lines(self):
        with self.assertRaises(layout.LayoutError):
            layout.normalize_layout([0], [0, 100, 200, 300])

    def test_deduplicates_and_sorts(self):
        h, v = layout.normalize_layout([200, 0, 100, 100], [300, 0, 100, 200])
        self.assertEqual(h, [0, 100, 200])
        self.assertEqual(v, [0, 100, 200, 300])

    def test_rejects_non_numeric(self):
        with self.assertRaises(layout.LayoutError):
            layout.normalize_layout([0, "abc", 100], [0, 100, 200, 300])

    def test_rejects_nan(self):
        with self.assertRaises(layout.LayoutError):
            layout.normalize_layout([0, float("nan"), 100], [0, 100, 200, 300])


def csv_bytes(text, encoding="utf-8"):
    return text.encode(encoding)


class CsvParseTests(unittest.TestCase):
    def test_chinese_header(self):
        parsed = parse_csv(csv_bytes(
            "框号,文字,x1,y1,x2,y2\n1,白砂糖,120,80,260,110\n2,一级,270,80,360,110\n"))
        self.assertEqual(len(parsed.boxes), 2)
        self.assertEqual(parsed.boxes[0]["box_ref"], "1")
        self.assertEqual(parsed.boxes[0]["text"], "白砂糖")
        self.assertEqual((parsed.boxes[0]["x1"], parsed.boxes[0]["y2"]), (120.0, 110.0))

    def test_rect_column(self):
        parsed = parse_csv(csv_bytes(
            '框号,文字,矩形坐标\n甲,白砂糖,"120,80,260,110"\n'))
        self.assertEqual(parsed.boxes[0]["box_ref"], "甲")
        self.assertEqual(parsed.boxes[0]["x2"], 260.0)

    def test_english_header_and_xywh(self):
        parsed = parse_csv(csv_bytes(
            "box,text,x,y,width,height\n7,糖,10,20,50,30\n"))
        self.assertEqual(parsed.boxes[0]["text"], "糖")
        self.assertEqual(parsed.boxes[0]["x2"], 60.0)
        self.assertEqual(parsed.boxes[0]["y2"], 50.0)

    def test_headerless_csv(self):
        parsed = parse_csv(csv_bytes("1,白砂糖,120,80,260,110\n"))
        self.assertEqual(parsed.boxes[0]["text"], "白砂糖")

    def test_gb18030_encoding(self):
        parsed = parse_csv(csv_bytes(
            "框号,文字,x1,y1,x2,y2\n1,白砂糖,1,2,3,4\n", "gb18030"))
        self.assertEqual(parsed.boxes[0]["text"], "白砂糖")

    def test_reversed_coordinates_are_normalised(self):
        parsed = parse_csv(csv_bytes(
            "框号,文字,x1,y1,x2,y2\n1,糖,100,50,10,10\n"))
        self.assertEqual((parsed.boxes[0]["x1"], parsed.boxes[0]["y2"]), (10.0, 50.0))

    def test_bad_row_is_reported_not_fatal(self):
        parsed = parse_csv(csv_bytes(
            '框号,文字,矩形坐标\n1,糖,"1,2,3,4"\n2,坏,"1,2"\n'))
        self.assertEqual(len(parsed.boxes), 1)
        self.assertTrue(parsed.warnings)

    def test_empty_file_rejected(self):
        with self.assertRaises(CsvError):
            parse_csv(b"")

    def test_missing_text_column_rejected(self):
        with self.assertRaises(CsvError):
            parse_csv(csv_bytes("框号,x1,y1,x2,y2\n1,1,2,3,4\n"))


if __name__ == "__main__":
    unittest.main()
