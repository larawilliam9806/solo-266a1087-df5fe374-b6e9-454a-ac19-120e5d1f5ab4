// Headless check of the expansion page: group-line marking, saved state,
// invalid-mark blocking and the expanded download.  Requires a live server.
// Usage: node tests/ui_expand.js <baseUrl>
const { JSDOM } = require("jsdom");

const base = process.argv[2] || "http://127.0.0.1:5057";
const CSV = `框号,文字,x1,y1,x2,y2
1,白砂糖,10,10,90,40
2,一级,110,10,190,40
3,5.00,210,10,290,40
4,〃,10,110,90,140
5,〃,110,110,190,140
6,4.80,210,110,290,140
7,红糖,10,210,90,240
8,二级,110,210,190,240
9,3.60,210,210,290,240
10,〃,10,310,90,340
11,〃,110,310,190,340
12,2.50,210,310,290,340
`;

function assert(condition, message) {
  if (!condition) throw new Error("FAIL: " + message);
  console.log("ok - " + message);
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function upload() {
  const form = new FormData();
  form.append("file", new Blob([CSV], { type: "text/csv" }), "page.csv");
  const response = await fetch(`${base}/upload`, { method: "POST", body: form, redirect: "manual" });
  return response.headers.get("location").split("/").pop();
}

async function open(jobId) {
  const page = await fetch(`${base}/jobs/${jobId}/expand`).then((r) => r.text());
  const dom = new JSDOM(page, {
    url: `${base}/jobs/${jobId}/expand`,
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
  });
  const { window } = dom;
  window.fetch = (url, options) => fetch(new URL(url, base), options);
  window.alert = (message) => { throw new Error("UI alert: " + message); };
  window.confirm = () => true;
  await new Promise((resolve) => window.addEventListener("load", resolve));
  await wait(300);
  return window;
}

const stateOf = (jobId) => fetch(`${base}/api/jobs/${jobId}`).then((r) => r.json());

(async () => {
  const jobId = await upload();
  await fetch(`${base}/api/jobs/${jobId}/lines`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      horizontal: [0, 100, 200, 300, 400], vertical: [0, 100, 200, 300],
    }),
  });

  let window = await open(jobId);
  let doc = window.document;
  let svg = doc.getElementById("canvas");

  const preview = () => Array.from(doc.querySelectorAll("#expanded-table tbody tr"))
    .map((tr) => Array.from(tr.children).slice(2, 5)
      .map((td) => td.textContent));
  const initial = preview();
  assert(initial[1][0] === "白砂糖" && initial[1][1] === "一级",
    "row 2 inherits 品名 and 规格");
  assert(initial[1][2] === "4.80", "批发价 keeps its original text");

  // Clicking an inner horizontal line drafts a group line.
  assert(!doc.querySelectorAll("#grid-lines .group-line").length,
    "no draft group lines at first");
  const hits = Array.from(svg.querySelectorAll("#line-hits .grid-hit"));
  const horizontalHits = hits.filter((hit) =>
    hit.getAttribute("x1") !== hit.getAttribute("x2"));
  assert(horizontalHits.length === 5, `5 horizontal hit targets (${horizontalHits.length})`);
  horizontalHits[1].dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await wait(80);
  assert(doc.querySelectorAll("#grid-lines .group-line").length === 1,
    "clicking an inner line drafts a group line");
  assert(doc.getElementById("group-summary").textContent.includes("草稿"),
    "unsaved group draft is announced");

  // Save it.
  doc.getElementById("groups-apply").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await wait(500);
  let saved = await stateOf(jobId);
  assert(saved.groups.valid.length === 1, "group line saved");
  assert(saved.groups.valid[0].position === 100, "saved at y=100");

  // Row 2 now starts a new group, so its "〃" has nothing to reuse.
  const after = preview();
  assert(after[1][0] === "" && after[1][1] === "",
    "group start clears inherited values");
  assert(doc.querySelectorAll("#expanded-table tbody tr.row-review").length >= 1,
    "unresolved row is marked for review");
  assert(doc.querySelector("#expanded-table tbody tr.row-review .reasons")
    .textContent.includes("行"), "review reason names the involved rows");

  // The download link works and contains only determined, priced rows.
  const link = doc.getElementById("download-expanded-link");
  assert(link.getAttribute("href") === `/jobs/${jobId}/export-expanded.csv`,
    "expanded download link is enabled");
  const csv = await fetch(`${base}${link.getAttribute("href")}`).then((r) => r.text());
  const lines = csv.replace(/^﻿/, "").trim().split(/\r?\n/);
  assert(lines[0] === "品名,规格,批发价,品名来源行,规格来源行", "expanded CSV header");
  assert(!lines.some((line) => line.includes("4.80")),
    "undetermined row is left out of the expanded CSV");

  // Removing the marked line through the lines API blocks the download.
  await fetch(`${base}/api/jobs/${jobId}/lines`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      horizontal: [0, 200, 300, 400], vertical: [0, 100, 200, 300],
    }),
  });
  window = await open(jobId);
  doc = window.document;
  assert(doc.querySelector("#grid-lines .group-invalid"), "lost mark is drawn in red");
  assert(doc.getElementById("group-pill").textContent.includes("需重选"),
    "status pill asks for a new choice");
  assert(doc.getElementById("expand-block").hidden === false,
    "download block notice is visible");
  assert(!doc.getElementById("download-expanded-link").getAttribute("href"),
    "expanded download is disabled");

  // Restoring the exact coordinate keeps the download blocked until the
  // clerk explicitly confirms again.
  await fetch(`${base}/api/jobs/${jobId}/lines`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      horizontal: [0, 100, 200, 300, 400], vertical: [0, 100, 200, 300],
    }),
  });
  const restored = await stateOf(jobId);
  assert(restored.groups.invalid.length === 1 && !restored.can_export_expanded,
    "same coordinate restored still needs confirmation");

  // The 撤销 button on the invalid entry clears the block.
  window = await open(jobId);
  doc = window.document;
  const invalidItem = doc.querySelector("#group-list li.invalid");
  const undo = Array.from(invalidItem.querySelectorAll("button"))
    .find((button) => button.textContent === "撤销");
  undo.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await wait(500);
  const cleared = await stateOf(jobId);
  assert(cleared.groups.invalid.length === 0 && cleared.can_export_expanded,
    "undoing the invalid mark unblocks the download");
  assert(doc.getElementById("download-expanded-link").getAttribute("href"),
    "download link re-enabled after undo");

  console.log("done");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
