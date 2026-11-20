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
