// Headless smoke test of the job page using jsdom against a live Flask server.
// Usage: node tests/ui_smoke.js <baseUrl> <jobId>
const { JSDOM } = require("jsdom");

const base = process.argv[2] || "http://127.0.0.1:5000";

// A two-row excerpt where box 5 straddles a column line.
const CSV = `框号,文字,x1,y1,x2,y2
1,白砂糖,10,10,90,40
2,一级,110,10,190,40
3,5.00,210,10,290,40
4,红糖,10,110,90,140
5,1.20,210,110,290,140
6,跨格,90,10,110,40
`;

async function upload() {
  const form = new FormData();
  form.append("file", new Blob([CSV], { type: "text/csv" }), "page.csv");
  const response = await fetch(`${base}/upload`, {
    method: "POST", body: form, redirect: "manual",
  });
  const jobId = response.headers.get("location").split("/").pop();
  await fetch(`${base}/api/jobs/${jobId}/lines`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      horizontal: [0, 100, 200], vertical: [0, 100, 200, 300],
    }),
  });
  return jobId;
}

function assert(condition, message) {
  if (!condition) throw new Error("FAIL: " + message);
  console.log("ok - " + message);
}

(async () => {
  const jobId = await upload();
  const page = await fetch(`${base}/jobs/${jobId}`).then((r) => r.text());
  const dom = new JSDOM(page, {
    url: `${base}/jobs/${jobId}`,
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
  });
  const { window } = dom;
  window.fetch = (url, options) =>
    fetch(new URL(url, base), options);
  window.alert = (message) => { throw new Error("UI alert: " + message); };

  await new Promise((resolve) => window.addEventListener("load", resolve));
  await new Promise((resolve) => setTimeout(resolve, 300));

  const doc = window.document;
  const svg = doc.getElementById("canvas");
  const boxes = svg.querySelectorAll("#boxes g.box");
  assert(boxes.length > 0, `renders ${boxes.length} box groups`);
  assert(svg.querySelectorAll("#grid-lines .grid-line.h").length >= 2,
    "renders at least two horizontal lines");
  assert(svg.querySelectorAll("#grid-lines .grid-line.v").length === 4,
    "renders four vertical lines");
  assert(svg.querySelectorAll("#line-hits .grid-hit").length >= 6,
    "renders draggable hit targets");
  const initial = doc.getElementById("initial-state").textContent;
  assert(initial.trim().startsWith("{"), "embeds initial state JSON");

  const state = JSON.parse(initial);
  const pending = state.boxes.filter(
    (box) => box.status === "pending" || box.status === "stale");
  assert(pending.length >= 1, "sample page has a pending box");

  // Select the pending box and use its inspector to assign it.
  const target = pending[0];
  const group = svg.querySelector(`g.box[data-box-id="${target.box_id}"]`);
  group.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 50));
  const select = doc.getElementById("target-cell");
  const assign = doc.getElementById("assign-cell");
  assert(select && assign, "inspector offers overlapping cells");
  const offered = Array.from(select.options).map((option) => option.value);
  const expected = target.candidates.map((candidate) => candidate.key);
  assert(JSON.stringify(offered.sort()) === JSON.stringify(expected.sort()),
    "inspector lists exactly the overlapping cells");

  assign.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 500));

  const after = JSON.parse(
    doc.getElementById("initial-state").textContent); // unchanged baseline
  const refreshed = await fetch(`${base}/api/jobs/${jobId}`).then((r) => r.json());
  assert(refreshed.pending === 0, "all boxes resolved after assignment");
  assert(refreshed.can_export === true, "export unlocked once nothing is pending");
  const link = doc.getElementById("download-link");
  assert(link.getAttribute("href") === `/jobs/${jobId}/export.csv`,
    "download link enabled");
  assert(after.boxes.length === state.boxes.length, "baseline boxes preserved");

  // Download and check the CSV contents.
  const csv = await fetch(`${base}${link.getAttribute("href")}`).then((r) => r.text());
  const rows = csv.replace(/^﻿/, "").trim().split(/\r?\n/);
  assert(rows[0] === "品名,规格,批发价", "CSV header is 品名,规格,批发价");
  assert(rows.length >= 3, `CSV has ${rows.length - 1} data rows`);
  // Typing coordinates updates the canvas without touching the saved layout.
  const verticalInput = doc.getElementById("vertical-input");
  verticalInput.value = "0, 100, 200, 300, 400";
  verticalInput.dispatchEvent(new window.Event("input", { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 80));
  assert(svg.querySelectorAll("#grid-lines .grid-line.v").length === 5,
    "typed coordinates redraw the draft grid");
  const untouched = await fetch(`${base}/api/jobs/${jobId}`).then((r) => r.json());
  assert(untouched.vertical.length === 4,
    "draft edits are not persisted until 应用格线");

  // 还原已应用 puts the saved lines back.
  doc.getElementById("reset-lines").dispatchEvent(
    new window.MouseEvent("click", { bubbles: true }));
  await new Promise((resolve) => setTimeout(resolve, 80));
  assert(svg.querySelectorAll("#grid-lines .grid-line.v").length === 4,
    "还原已应用 restores the saved verticals");

  console.log("done");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
