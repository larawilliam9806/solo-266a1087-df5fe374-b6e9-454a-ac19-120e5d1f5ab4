import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pricing import create_app  # noqa: E402

SAMPLE = """框号,文字,x1,y1,x2,y2
1,白砂糖,10,10,90,40
2,一级,110,10,190,40
3,5.00,210,10,290,40
4,红糖,10,110,90,140
5,1.20,210,110,290,140
6,跨格,90,10,110,40
7,出界,400,400,450,430
"""


class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "test.sqlite3")
        self.app = create_app({
            "TESTING": True,
            "DATABASE": self.db_path,
            "SECRET_KEY": "test",
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def upload(self, body=SAMPLE, filename="page.csv"):
        return self.client.post(
            "/upload",
            data={"file": (io.BytesIO(body.encode("utf-8")), filename)},
            content_type="multipart/form-data",
            follow_redirects=False,
        )

    def upload_and_get_job(self):
        response = self.upload()
        self.assertEqual(response.status_code, 302)
        return int(response.headers["Location"].rstrip("/").split("/")[-1])

    def set_lines(self, job_id, horizontal, vertical):
        return self.client.post(
            f"/api/jobs/{job_id}/lines",
            json={"horizontal": horizontal, "vertical": vertical},
        )

    def state(self, job_id):
        response = self.client.get(f"/api/jobs/{job_id}")
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def box_id(self, state, ref):
        return next(item["box_id"] for item in state["boxes"] if item["box_ref"] == ref)


class UploadTests(AppTestCase):
    def test_upload_creates_job_and_redirects(self):
        job_id = self.upload_and_get_job()
        state = self.state(job_id)
        self.assertEqual(len(state["boxes"]), 7)
        self.assertIn(state["boxes"][0]["status"],
                      ("assigned_auto", "pending", "stale"))

    def test_upload_rejects_empty_file(self):
        response = self.client.post(
            "/upload",
            data={"file": (io.BytesIO(b""), "empty.csv")},
            content_type="multipart/form-data",
            follow_redirects=True,
        )
        self.assertIn("文件为空".encode("utf-8"), response.data)

    def test_upload_without_file_redirects_with_error(self):
        response = self.client.post("/upload", data={}, follow_redirects=True)
        self.assertIn("请选择一个 CSV 文件".encode("utf-8"), response.data)

    def test_job_page_renders(self):
        job_id = self.upload_and_get_job()
        response = self.client.get(f"/jobs/{job_id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"grid-lines", response.data)


class LineTests(AppTestCase):
    def test_invalid_lines_keep_previous_layout_and_assignments(self):
        job_id = self.upload_and_get_job()
        self.assertEqual(self.set_lines(job_id, [0, 100, 200], [0, 100, 200, 300])
                         .status_code, 200)
        before = self.state(job_id)
        response = self.set_lines(job_id, [0, 100], [0, 100, 200])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["ok"])
        after = self.state(job_id)
        self.assertEqual(before["horizontal"], after["horizontal"])
        self.assertEqual(before["vertical"], after["vertical"])
        self.assertEqual([(b["box_id"], b["status"]) for b in before["boxes"]],
                         [(b["box_id"], b["status"]) for b in after["boxes"]])

    def test_stale_pending_decisions_after_boundary_move(self):
        job_id = self.upload_and_get_job()
        self.set_lines(job_id, [0, 100, 200], [0, 100, 200, 300])
        state = self.state(job_id)
        box = self.box_id(state, "6")
        decision = self.client.post(
            f"/api/jobs/{job_id}/decisions",
            json={"box_id": box, "kind": "cell", "target_cell": "100|0|200|100"},
        )
        self.assertEqual(decision.status_code, 200)
        self.assertEqual(self.state(job_id)["status_counts"].get("assigned_manual"), 1)

        self.set_lines(job_id, [0, 100, 200], [0, 95, 200, 300])
        state = self.state(job_id)
        item = next(entry for entry in state["boxes"] if entry["box_id"] == box)
        self.assertEqual(item["status"], "stale")

    def test_decision_survives_unrelated_line_change(self):
        job_id = self.upload_and_get_job()
        self.set_lines(job_id, [0, 100, 200], [0, 100, 200, 300])
        box = self.box_id(self.state(job_id), "6")
        self.client.post(f"/api/jobs/{job_id}/decisions",
                         json={"box_id": box, "kind": "cell",
                               "target_cell": "100|0|200|100"})
        # Moving the bottom border y=200 does not touch box 6's cells.
        self.set_lines(job_id, [0, 100, 260], [0, 100, 200, 300])
        item = next(entry for entry in self.state(job_id)["boxes"]
                    if entry["box_id"] == box)
        self.assertEqual(item["status"], "assigned_manual")


class DecisionTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.job = self.upload_and_get_job()
        self.set_lines(self.job, [0, 100, 200], [0, 100, 200, 300])
        self.current = self.state(self.job)

    def test_accepts_overlapping_cell(self):
        box = self.box_id(self.current, "6")
        response = self.client.post(
            f"/api/jobs/{self.job}/decisions",
            json={"box_id": box, "kind": "cell", "target_cell": "0|0|100|100"},
        )
        self.assertEqual(response.status_code, 200)
        item = next(entry for entry in response.get_json()["boxes"]
                    if entry["box_id"] == box)
        self.assertEqual(item["status"], "assigned_manual")
        self.assertEqual(item["column"], "name")

    def test_rejects_non_overlapping_cell(self):
        box = self.box_id(self.current, "6")
        response = self.client.post(
            f"/api/jobs/{self.job}/decisions",
            json={"box_id": box, "kind": "cell", "target_cell": "200|100|300|200"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.get_json()["ok"])

    def test_rejects_decision_for_auto_box(self):
        box = self.box_id(self.current, "1")
        response = self.client.post(
            f"/api/jobs/{self.job}/decisions",
            json={"box_id": box, "kind": "cell", "target_cell": "0|0|100|100"},
        )
        self.assertEqual(response.status_code, 409)

    def test_exclude_box(self):
        box = self.box_id(self.current, "7")
        response = self.client.post(
            f"/api/jobs/{self.job}/decisions",
            json={"box_id": box, "kind": "exclude"},
        )
        self.assertEqual(response.status_code, 200)
        item = next(entry for entry in response.get_json()["boxes"]
                    if entry["box_id"] == box)
        self.assertEqual(item["status"], "excluded")

    def test_unknown_box_404(self):
        response = self.client.post(
            f"/api/jobs/{self.job}/decisions",
            json={"box_id": 99999, "kind": "exclude"},
        )
        self.assertEqual(response.status_code, 404)

    def test_clear_decision_returns_to_pending(self):
        box = self.box_id(self.current, "7")
        self.client.post(f"/api/jobs/{self.job}/decisions",
                         json={"box_id": box, "kind": "exclude"})
        response = self.client.delete(f"/api/jobs/{self.job}/decisions/{box}")
        self.assertEqual(response.status_code, 200)
        item = next(entry for entry in response.get_json()["boxes"]
                    if entry["box_id"] == box)
        self.assertEqual(item["status"], "pending")


class ExportTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.job = self.upload_and_get_job()
        self.set_lines(self.job, [0, 100, 200], [0, 100, 200, 300])

    def resolve_all(self):
        state = self.state(self.job)
        box6 = self.box_id(state, "6")
        box7 = self.box_id(state, "7")
        self.client.post(f"/api/jobs/{self.job}/decisions",
                         json={"box_id": box6, "kind": "cell",
                               "target_cell": "100|0|200|100"})
        self.client.post(f"/api/jobs/{self.job}/decisions",
                         json={"box_id": box7, "kind": "exclude"})

    def test_export_blocked_while_pending(self):
        response = self.client.get(f"/jobs/{self.job}/export.csv")
        self.assertEqual(response.status_code, 409)

    def test_export_blocked_without_valid_lines(self):
        self.set_lines(self.job, [0, 100], [0, 100, 200])
        response = self.client.get(f"/jobs/{self.job}/export.csv")
        self.assertEqual(response.status_code, 409)

    def test_export_succeeds_when_all_resolved(self):
        self.resolve_all()
        response = self.client.get(f"/jobs/{self.job}/export.csv")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.headers["Content-Type"])
        text = response.data.decode("utf-8-sig")
        lines = [line for line in text.splitlines() if line.strip()]
        self.assertEqual(lines[0], "品名,规格,批发价")
        self.assertEqual(lines[1], "白砂糖,跨格一级,5.00")
        self.assertEqual(lines[2], "红糖,,1.20")

    def test_export_filename_is_utf8_encoded(self):
        self.resolve_all()
        response = self.client.get(f"/jobs/{self.job}/export.csv")
        disposition = response.headers["Content-Disposition"]
        self.assertIn("filename*=UTF-8''", disposition)
        self.assertIn("%E4%BB%B7%E7%9B%AE%E8%A1%A8", disposition)

    def test_export_uses_bom(self):
        self.resolve_all()
        response = self.client.get(f"/jobs/{self.job}/export.csv")
        self.assertTrue(response.data.startswith(b"\xef\xbb\xbf"))


class ExpansionTests(AppTestCase):
    """Group lines, shorthand expansion and the expanded download."""

    # 7 grid rows:
    # 1 白砂糖 / 一级 / 5.00      (origin)
    # 2 〃    / 〃   / 4.80        (inherits both from 1)
    # 3 红糖   / 空白 / 3.60      (blank spec breaks the chain)
    # 4 〃    / 空白 / 2.90        (name from 3, spec genuinely blank)
    # 5 〃    / 〃   / 1.50        (spec has nothing to reuse -> review)
    # 6 白糖   / 二级 / 空白      (determined but no price -> skipped)
    # 7 〃    / 〃   / 0.90        (inherits from 6)
    CSV = """框号,文字,x1,y1,x2,y2
1,白砂糖,10,10,90,40
2,一级,110,10,190,40
3,5.00,210,10,290,40
4,〃,10,110,90,140
5,〃,110,110,190,140
6,4.80,210,110,290,140
7,红糖,10,210,90,240
8,3.60,210,210,290,240
9,〃,10,310,90,340
10,2.90,210,310,290,340
11,〃,10,410,90,440
12,〃,110,410,190,440
13,1.50,210,410,290,440
14,白糖,10,510,90,540
15,二级,110,510,190,540
16,〃,10,610,90,640
17,〃,110,610,190,640
18,0.90,210,610,290,640
"""
    H = [0, 100, 200, 300, 400, 500, 600, 700]
    V = [0, 100, 200, 300]

    def setUp(self):
        super().setUp()
        job = self.upload(self.CSV, "expand.csv")
        self.job = int(job.headers["Location"].rstrip("/").split("/")[-1])
        self.set_lines(self.job, self.H, self.V)

    def set_groups(self, positions):
        return self.client.post(f"/api/jobs/{self.job}/groups",
                                json={"lines": [{"position": p} for p in positions]})

    def expansion(self, job=None):
        state = self.state(job or self.job)
        return {row["row"]: row for row in state["expansion"]}

    def test_shorthand_inherits_previous_row_within_group(self):
        rows = self.expansion()
        self.assertEqual((rows[2]["name"], rows[2]["spec"]), ("白砂糖", "一级"))
        self.assertEqual(rows[2]["name_source"], 1)
        self.assertEqual(rows[2]["spec_source"], 1)
        # 批发价 keeps its own text and is never inherited.
        self.assertEqual(rows[2]["price"], "4.80")
        self.assertNotIn("price_source", rows[2])
        # The origin row has no inherited value, so no source row.
        self.assertIsNone(rows[1]["name_source"])

    def test_only_wholly_shorthand_cells_inherit(self):
        body = self.CSV.replace("4,〃,10,110,90,140", "4,〃 附注,10,110,90,140")
        job = int(self.upload(body, "mixed.csv")
                  .headers["Location"].rstrip("/").split("/")[-1])
        self.set_lines(job, self.H, self.V)
        row = self.expansion(job)[2]
        self.assertEqual(row["name"], "〃 附注")
        self.assertEqual(row["status"], "determined")

    def test_blank_cell_breaks_inheritance(self):
        rows = self.expansion()
        # Row 3's spec is genuinely blank; row 4's spec is blank too.
        self.assertEqual(rows[3]["status"], "determined")
        self.assertEqual(rows[4]["spec"], "")
        # Row 5's "〃" spec has a blank cell above it: the chain is broken and
        # both the blank row and the affected row are named.
        self.assertEqual(rows[5]["status"], "review")
        reasons = [reason["reason"] for reason in rows[5]["reasons"]]
        self.assertTrue(any("空白" in reason for reason in reasons))
        self.assertIn(4, {number for reason in rows[5]["reasons"]
                          for number in reason["rows"]})

    def test_group_line_stops_inheritance(self):
        response = self.set_groups([300])   # starts grid row 4
        self.assertEqual(response.status_code, 200)
        rows = {row["row"]: row for row in response.get_json()["expansion"]}
        self.assertEqual(rows[4]["group"], 2)
        # Row 4's "〃" is the group's first row: nothing to reuse.
        self.assertEqual(rows[4]["status"], "review")
        self.assertEqual(rows[4]["reasons"][0]["rows"], [4])
        # Row 4's spec cell is blank, so row 5's "〃" spec has no source.
        self.assertEqual(rows[5]["status"], "review")

    def test_group_line_must_be_an_inner_horizontal(self):
        for value in (0, 700, 123):
            response = self.set_groups([value])
            self.assertEqual(response.status_code, 400, value)
            self.assertFalse(response.get_json()["ok"])

    def test_removing_marked_line_blocks_expanded_download(self):
        self.set_groups([300])
        self.assertEqual(
            self.client.get(f"/jobs/{self.job}/export-expanded.csv").status_code,
            200,
        )
        self.set_lines(self.job, [0, 100, 200, 400, 500, 600, 700], self.V)
        state = self.state(self.job)
        self.assertTrue(state["groups"]["invalid"])
        self.assertFalse(state["can_export_expanded"])
        self.assertIn("内横线", state["expand_block"])
        response = self.client.get(f"/jobs/{self.job}/export-expanded.csv")
        self.assertEqual(response.status_code, 409)

    def test_restoring_same_coordinate_still_needs_confirmation(self):
        self.set_groups([300])
        self.set_lines(self.job, [0, 100, 200, 400, 500, 600, 700], self.V)
        self.assertIsNone(self.state(self.job)["groups"]["invalid"][0]["row"])
        # Put the line back exactly where it was: the old confirmation must
        # not silently revive; the download stays blocked.
        self.set_lines(self.job, self.H, self.V)
        state = self.state(self.job)
        self.assertTrue(state["groups"]["invalid"])
        self.assertFalse(state["can_export_expanded"])
        self.assertEqual(
            self.client.get(f"/jobs/{self.job}/export-expanded.csv").status_code,
            409,
        )
        # Undoing clears the block.
        self.assertEqual(self.set_groups([]).status_code, 200)
        self.assertFalse(self.state(self.job)["groups"]["invalid"])
        self.assertTrue(self.state(self.job)["can_export_expanded"])

    def test_confirmation_clears_invalid_mark(self):
        self.set_groups([300])
        self.set_lines(self.job, [0, 100, 200, 400, 500, 600, 700], self.V)
        self.set_lines(self.job, self.H, self.V)
        response = self.set_groups([300])
        self.assertEqual(response.status_code, 200)
        state = response.get_json()
        self.assertFalse(state["groups"]["invalid"])
        self.assertTrue(state["can_export_expanded"])

    def test_confirmation_clears_only_the_mark_it_names(self):
        self.set_groups([300, 600])
        self.set_lines(self.job, [0, 100, 200, 400, 500, 700], self.V)
        self.assertEqual(len(self.state(self.job)["groups"]["invalid"]), 2)
        self.set_lines(self.job, self.H, self.V)
        # Confirm only y=300 and explicitly hand back y=600: the untouched
        # lost mark stays flagged.
        response = self.client.post(
            f"/api/jobs/{self.job}/groups",
            json={"lines": [{"position": 300}], "keep_invalid": [600]},
        )
        self.assertEqual(response.status_code, 200)
        state = response.get_json()
        self.assertEqual([mark["position"] for mark in state["groups"]["valid"]],
                         [300])
        self.assertEqual([mark["position"] for mark in state["groups"]["invalid"]],
                         [600])
        self.assertFalse(state["can_export_expanded"])
        # Omitting a mark drops it: the page lists revoked marks as excluded.
        response = self.client.post(
            f"/api/jobs/{self.job}/groups",
            json={"lines": [{"position": 300}], "keep_invalid": []},
        )
        self.assertEqual(response.get_json()["groups"]["invalid"], [])
        self.assertTrue(response.get_json()["can_export_expanded"])

    def test_expanded_export_skips_review_and_blank_price_rows(self):
        response = self.client.get(f"/jobs/{self.job}/export-expanded.csv")
        self.assertEqual(response.status_code, 200)
        text = response.data.decode("utf-8-sig")
        lines = [line for line in text.splitlines() if line.strip()]
        self.assertEqual(lines[0], "品名,规格,批发价,品名来源行,规格来源行")
        # Origin rows keep an empty source column; inherited rows name row 1.
        self.assertEqual(lines[1], "白砂糖,一级,5.00,,")
        self.assertEqual(lines[2], "白砂糖,一级,4.80,1,1")
        # Rows 3 and 4 export with an empty spec; row 5 is undetermined and
        # row 6 has no price, so row 5's 1.50 never reaches the file.
        self.assertIn("红糖,,3.60,,", text)
        self.assertIn("红糖,,2.90,3,", text)
        self.assertNotIn("1.50", text)
        # Row 7 inherits from row 6 and is exported.
        self.assertEqual(lines[-1], "白糖,二级,0.90,6,6")

    def test_expanded_export_does_not_require_every_box_resolved(self):
        state = self.state(self.job)
        self.assertEqual(state["pending"], 0)
        self.assertTrue(state["can_export_expanded"])
        response = self.client.get(f"/jobs/{self.job}/export-expanded.csv")
        self.assertEqual(response.status_code, 200)

    def test_expand_page_renders(self):
        response = self.client.get(f"/jobs/{self.job}/expand")
        self.assertEqual(response.status_code, 200)
        self.assertIn("展开表逐行预览".encode("utf-8"), response.data)
        self.assertIn(b"download-expanded-link", response.data)
        # The entry page links to the expansion page.
        self.assertIn(f'/jobs/{self.job}/expand'.encode(),
                      self.client.get(f"/jobs/{self.job}").data)

    def test_pending_box_marks_affected_and_inheriting_rows(self):
        body = self.CSV + "19,跨格,90,110,410,140\n"
        response = self.upload(body, "pending.csv")
        job = int(response.headers["Location"].rstrip("/").split("/")[-1])
        self.set_lines(job, self.H, self.V)
        state = self.state(job)
        self.assertEqual(state["pending"], 1)
        rows = {row["row"]: row for row in state["expansion"]}
        # Row 2 is where the pending box may land.
        self.assertEqual(rows[2]["status"], "review")
        self.assertTrue(any(reason.get("boxes") for reason in rows[2]["reasons"]))
        # Row 2 inherits from row 1, so the row with the pending box is named.
        self.assertIn(2, {number for reason in rows[2]["reasons"]
                          for number in reason["rows"]})
        # Row 7 inherits from row 6; the pending box elsewhere does not leak
        # into unrelated groups, but its own group already ends at row 2.

    def test_pending_box_affects_later_inheritors_in_the_same_group(self):
        body = self.CSV + "19,跨格,90,510,410,540\n"  # may land in row 6
        response = self.upload(body, "pending2.csv")
        job = int(response.headers["Location"].rstrip("/").split("/")[-1])
        self.set_lines(job, self.H, self.V)
        rows = self.expansion(job)
        self.assertEqual(rows[6]["status"], "review")
        self.assertEqual(rows[7]["status"], "review")
        self.assertTrue(any("沿用" in reason["reason"]
                            for reason in rows[7]["reasons"]))

    def test_revision_advances_on_every_edit(self):
        before = self.state(self.job)["revision"]
        self.set_groups([300])
        after = self.state(self.job)["revision"]
        self.assertGreater(after, before)
        self.set_lines(self.job, self.H, self.V)
        self.assertGreater(self.state(self.job)["revision"], after)


class JobManagementTests(AppTestCase):
    def test_delete_job_removes_it(self):
        job_id = self.upload_and_get_job()
        response = self.client.post(f"/jobs/{job_id}/delete", follow_redirects=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(f"/api/jobs/{job_id}").status_code, 404)

    def test_missing_job_returns_404_page(self):
        response = self.client.get("/jobs/99999")
        self.assertEqual(response.status_code, 404)

    def test_suggest_lines_returns_three_columns(self):
        job_id = self.upload_and_get_job()
        response = self.client.post(f"/api/jobs/{job_id}/suggest-lines")
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(len(data["vertical"]), 4)
        self.assertGreaterEqual(len(data["horizontal"]), 2)


if __name__ == "__main__":
    unittest.main()
