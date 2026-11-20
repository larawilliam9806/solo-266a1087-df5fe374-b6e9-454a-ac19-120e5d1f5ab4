// Headless check of the grid-line editing workflow (typed coordinates, apply,
// invalid-line rejection, decision invalidation).  Requires a live server.
// Usage: node tests/ui_lines.js <baseUrl>
const { JSDOM } = require("jsdom");

const base = process.argv[2] || "http://127.0.0.1:5057";
const CSV = `框号,文字,x1,y1,x2,y2
1,白砂糖,10,10,90,40
2,一级,110,10,190,40
3,5.00,210,10,290,40
4,红糖,10,110,90,140
5,跨格,90,10,110,40
6,出界,400,400,450,430
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
  const location = response.headers.get("location");
  return location.split("/").pop();
}

async function open(jobId) {
  const page = await fetch(`${base}/jobs/${jobId}`).then((r) => r.text());
  const dom = new JSDOM(page, {
    url: `${base}/jobs/${jobId}`,
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
  });
  const { window } = dom;
  window.fetch = (url, options) => fetch(new URL(url, base), options);
  window.alert = (message) => { throw new Error("UI alert: " + message); };
  await new Promise((resolve) => window.addEventListener("load", resolve));
  await wait(300);
  return window;
}

async function state(jobId) {
  return fetch(`${base}/api/jobs/${jobId}`).then((r) => r.json());
}

function click(window, node) {
  node.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
}

(async () => {
  const jobId = await upload();
  // Pin a known grid: box 5 straddles a vertical line, box 6 is off-page.
  await fetch(`${base}/api/jobs/${jobId}/lines`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ horizontal: [0, 100, 200], vertical: [0, 100, 200, 300] }),
  });
  const window = await open(jobId);

  {
    const before = await state(jobId);
    assert(before.status_counts.pending >= 1, "boxes start pending");
    for (const box of before.boxes.filter((b) => b.status === "pending" || b.status === "stale")) {
      await fetch(`${base}/api/jobs/${jobId}/decisions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          box_id: box.box_id,
          kind: box.candidates.length ? "cell" : "exclude",
          target_cell: box.candidates.length ? box.candidates[0].key : undefined,
        }),
      });
    }
    const resolved = await state(jobId);
    assert(resolved.pending === 0 && resolved.can_export, "all boxes resolved");
  }

  // Re-open to pick up resolved state.
  const page = await open(jobId);
  const doc2 = page.document;
  const vertical = doc2.getElementById("vertical-input");
  const horizontal = doc2.getElementById("horizontal-input");
  const apply = doc2.getElementById("apply-lines");

  const verticalCount = () => doc2.querySelectorAll("#grid-lines .grid-line.v").length;

  // 1. Invalid lines are refused and the drawing is untouched.
  vertical.value = "0, 100";
  vertical.dispatchEvent(new page.Event("input", { bubbles: true }));
  await wait(50);
  click(page, apply);
  await wait(400);
  const error = doc2.getElementById("line-error");
  assert(!error.hidden && error.textContent.includes("竖线"),
    "invalid vertical count surfaces a Chinese error");
  const afterInvalid = await state(jobId);
  assert(afterInvalid.vertical.length === 4 && afterInvalid.can_export,
    "invalid submit keeps previous lines and assignments");
  assert(verticalCount() === 4, "canvas still draws the four saved verticals");

  // 2. Moving an unrelated line keeps the manual decisions.
  horizontal.value = "0, 100, 260";
  horizontal.dispatchEvent(new page.Event("input", { bubbles: true }));
  await wait(50);
  click(page, apply);
  await wait(400);
  const moved = await state(jobId);
  assert(!moved.horizontal.includes(200) && moved.horizontal.includes(260),
    "new horizontal line applied");
  assert(moved.grid_valid, "grid stays valid");
  assert(moved.pending === 0, "unrelated line move keeps decisions valid");

  // 3. Adding a line across the decided cells sends the decision back.
  horizontal.value = "0, 25, 100, 260";
  horizontal.dispatchEvent(new page.Event("input", { bubbles: true }));
  await wait(50);
  click(page, apply);
  await wait(400);
  const split = await state(jobId);
  assert(split.pending >= 1, "line splitting a decided cell returns it to pending");
  assert(split.can_export === false, "export re-locked while a box is pending");
  const pill = doc2.getElementById("status-pill");
  assert(pill.textContent.includes("待核"), "status pill reports pending boxes");

  // 4. Restoring the earlier lines restores the decision (no retyping).
  horizontal.value = "0, 100, 260";
  horizontal.dispatchEvent(new page.Event("input", { bubbles: true }));
  await wait(50);
  click(page, apply);
  await wait(400);
  const restored = await state(jobId);
  assert(restored.pending === 0, "restoring the layout re-validates the stored decision");

  console.log("done");
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
