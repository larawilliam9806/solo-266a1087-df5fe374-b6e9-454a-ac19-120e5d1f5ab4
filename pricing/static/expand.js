// Expansion page: mark inner horizontal ruling lines as group dividers and
// preview the expanded ("〃" / "同上") table.  The SVG is read-only here --
// grid editing stays on the entry page.
(function () {
  "use strict";

  const viewer = document.getElementById("viewer");
  if (!viewer) return;

  const jobId = viewer.dataset.jobId;
  const svg = document.getElementById("canvas");
  const gridLayer = document.getElementById("grid-lines");
  const boxLayer = document.getElementById("boxes");
  const hitLayer = document.getElementById("line-hits");
  const SVG_NS = "http://www.w3.org/2000/svg";

  const STATUS_COLORS = {
    assigned_auto: "#2f6b48",
    assigned_manual: "#1f5f8b",
    pending: "#b3261e",
    stale: "#a35a00",
    excluded: "#7a7266",
  };

  let state = null;
  let groupDraft = [];             // y coords of draft group lines
  let revoked = [];                // invalid marks the clerk chose to undo
  let bounds = { minX: 0, minY: 0, maxX: 1, maxY: 1 };

  function el(name, attrs) {
    const node = document.createElementNS(SVG_NS, name);
    Object.keys(attrs || {}).forEach(function (key) {
      node.setAttribute(key, attrs[key]);
    });
    return node;
  }

  function sortNumbers(list) {
    return list.slice().sort(function (a, b) { return a - b; });
  }

  function sameValue(a, b) {
    return Math.abs(a - b) < 1e-6;
  }

  function request(url, options) {
    return fetch(url, Object.assign({ credentials: "same-origin" }, options || {}))
      .then(function (response) {
        return response.text().then(function (text) {
          let data = {};
          try { data = JSON.parse(text); } catch (e) { data = {}; }
          if (!response.ok || data.ok === false) {
            throw new Error(
              [data.error || ("请求失败（" + response.status + "）")]
                .concat(data.details || []).join("\n")
            );
          }
          return data;
        });
      });
  }

  function fail(error) {
    window.alert(error.message || String(error));
  }

  // ---------------------------------------------------------------- layout
  function computeBounds() {
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
    state.boxes.forEach(function (box) {
      minX = Math.min(minX, box.x1); minY = Math.min(minY, box.y1);
      maxX = Math.max(maxX, box.x2); maxY = Math.max(maxY, box.y2);
    });
    state.horizontal.forEach(function (y) {
      minY = Math.min(minY, y); maxY = Math.max(maxY, y);
    });
    state.vertical.forEach(function (x) {
      minX = Math.min(minX, x); maxX = Math.max(maxX, x);
    });
    if (!isFinite(minX)) { minX = 0; minY = 0; maxX = 800; maxY = 1000; }
    const padX = Math.max((maxX - minX) * 0.05, 10);
    const padY = Math.max((maxY - minY) * 0.05, 10);
    return { minX: minX - padX, minY: minY - padY, maxX: maxX + padX, maxY: maxY + padY };
  }

  function render() {
    bounds = computeBounds();
    const width = Math.max(bounds.maxX - bounds.minX, 1);
    const height = Math.max(bounds.maxY - bounds.minY, 1);
    svg.setAttribute("viewBox", [bounds.minX, bounds.minY, width, height].join(" "));
    svg.setAttribute("width", width);
    svg.setAttribute("height", height);
    svg.style.aspectRatio = width + " / " + height;

    const bg = document.getElementById("page-bg");
    bg.setAttribute("x", bounds.minX);
    bg.setAttribute("y", bounds.minY);
    bg.setAttribute("width", width);
    bg.setAttribute("height", height);

    gridLayer.innerHTML = "";
    boxLayer.innerHTML = "";
    hitLayer.innerHTML = "";

    state.horizontal.forEach(function (y, index) {
      gridLayer.appendChild(makeLine("h", y, index));
    });
    state.vertical.forEach(function (x, index) {
      gridLayer.appendChild(makeLine("v", x, index));
    });

    groupDraft.forEach(function (y) {
      const thickness = Math.max(height * 0.008, 3);
      gridLayer.appendChild(el("rect", {
        x: bounds.minX,
        y: y - thickness / 2,
        width: width,
        height: thickness,
        class: "group-line",
        "pointer-events": "none",
      }));
      const badge = el("text", {
        x: bounds.minX + Math.max(width * 0.008, 4),
        y: y - Math.max(height * 0.012, 4),
        class: "group-badge",
        "pointer-events": "none",
      });
      badge.textContent = "分组";
      gridLayer.appendChild(badge);
    });

    state.groups.invalid.forEach(function (mark) {
      gridLayer.appendChild(el("line", {
        x1: bounds.minX, y1: mark.position,
        x2: bounds.maxX, y2: mark.position,
        class: "group-invalid",
        "pointer-events": "none",
      }));
    });

    state.boxes.forEach(function (box) {
      const color = STATUS_COLORS[box.status] || "#999";
      const group = el("g", { "data-box-id": box.box_id, class: "box" });
      group.appendChild(el("rect", {
        x: box.x1, y: box.y1,
        width: Math.max(box.x2 - box.x1, 0.5),
        height: Math.max(box.y2 - box.y1, 0.5),
        rx: 1.5,
        fill: color,
        "fill-opacity": box.status === "excluded" ? "0.10" : "0.18",
        stroke: color,
        "stroke-width": 1.4,
        "stroke-dasharray": (box.status === "excluded" || box.status === "stale") ? "4 2" : "none",
        "pointer-events": "none",
      }));
      if (box.text) {
        const room = Math.max(box.y2 - box.y1, 1);
        const label = el("text", {
          x: box.x1 + 2,
          y: box.y1 + Math.max(room * 0.72, 4),
          class: "box-label",
        });
        label.setAttribute("font-size", Math.max(Math.min(room * 0.6, 22), 3));
        label.textContent = box.text.length > 24 ? box.text.slice(0, 24) + "…" : box.text;
        group.appendChild(label);
      }
      boxLayer.appendChild(group);
    });
  }

  function makeLine(orientation, value, index) {
    const isH = orientation === "h";
    const line = el("line", isH
      ? { x1: bounds.minX, y1: value, x2: bounds.maxX, y2: value, class: "grid-line h" }
      : { x1: value, y1: bounds.minY, x2: value, y2: bounds.maxY, class: "grid-line v" });
    const hit = el("line", {
      x1: line.getAttribute("x1"), y1: line.getAttribute("y1"),
      x2: line.getAttribute("x2"), y2: line.getAttribute("y2"),
      class: "grid-hit",
    });
    if (isH) {
      hit.addEventListener("click", function (event) {
        event.preventDefault();
        event.stopPropagation();
        toggleGroup(value);
      });
    } else {
      hit.style.cursor = "default";
    }
    const group = el("g", {});
    group.appendChild(line);
    hitLayer.appendChild(hit);
    return group;
  }

  function toggleGroup(value) {
    const inner = state.horizontal.slice(1, -1);
    if (!inner.some(function (line) { return sameValue(line, value); })) return;
    if (groupDraft.some(function (line) { return sameValue(line, value); })) {
      groupDraft = groupDraft.filter(function (line) { return !sameValue(line, value); });
    } else {
      groupDraft = sortNumbers(groupDraft.concat([value]));
    }
    render();
    renderGroups();
  }

  // ---------------------------------------------------------------- groups
  function savedPositions() {
    return sortNumbers(state.groups.valid.map(function (item) { return item.position; }));
  }

  function groupsMatchState() {
    return groupDraft.join(",") === savedPositions().join(",");
  }

  function renderGroups() {
    const saved = state.groups.valid;
    const invalid = state.groups.invalid;
    const dirty = !groupsMatchState();

    const pill = document.getElementById("group-pill");
    if (invalid.length) {
      pill.className = "pill bad";
      pill.textContent = invalid.length + " 条分组线需重选或撤销";
    } else if (saved.length) {
      pill.className = "pill good";
      pill.textContent = saved.length + " 条分组线已确认";
    } else {
      pill.className = "pill warn";
      pill.textContent = "未分组";
    }
    document.getElementById("group-summary").textContent = dirty
      ? "（草稿未保存，点“应用分组线”生效）"
      : "点击内横线增删草稿；应用分组线会保存当前草稿。";

    const list = document.getElementById("group-list");
    list.innerHTML = "";
    if (!saved.length && !invalid.length) {
      const empty = document.createElement("li");
      empty.className = "muted";
      empty.textContent = "还没有分组线。";
      list.appendChild(empty);
    }
    saved.forEach(function (mark, index) {
      const item = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = "第 " + (index + 1) + " 组开始：y=" + mark.position +
        "（第 " + mark.row + " 行与第 " + (mark.row + 1) + " 行之间）";
      item.appendChild(label);
      const undo = document.createElement("button");
      undo.type = "button";
      undo.textContent = "撤销";
      undo.addEventListener("click", function () {
        groupDraft = groupDraft.filter(function (value) {
          return !sameValue(value, mark.position);
        });
        saveGroups();
      });
      item.appendChild(undo);
      list.appendChild(item);
    });
    invalid.forEach(function (mark) {
      const item = document.createElement("li");
      item.className = "invalid";
      const label = document.createElement("span");
      label.textContent = "y=" + mark.position + "：" + mark.reason +
        (mark.row ? "（该位置又是内横线，需重新确认）" : "");
      item.appendChild(label);
      if (mark.row) {
        const retry = document.createElement("button");
        retry.type = "button";
        retry.textContent = "重选为分组线";
        retry.addEventListener("click", function () {
          groupDraft = sortNumbers(groupDraft.concat([mark.position]));
          saveGroups();
        });
        item.appendChild(retry);
      }
      const drop = document.createElement("button");
      drop.type = "button";
      drop.className = "danger";
      drop.textContent = "撤销";
      drop.addEventListener("click", function () {
        revoked = sortNumbers(revoked.concat([mark.position]));
        saveGroups();
      });
      item.appendChild(drop);
      list.appendChild(item);
    });

    const block = document.getElementById("expand-block");
    block.hidden = !state.expand_block;
    if (state.expand_block) block.textContent = state.expand_block;
  }

  // Marks that are still saved (valid or invalid) but not in groupDraft are
  // handed back as keep_invalid, so they stay flagged until handled.
  function keepInvalid() {
    return state.groups.invalid
      .filter(function (mark) {
        const handled = revoked.concat(groupDraft).some(function (value) {
          return sameValue(value, mark.position);
        });
        return !handled;
      })
      .map(function (mark) { return mark.position; });
  }

  function saveGroups() {
    const positions = sortNumbers(groupDraft.slice());
    request("/api/jobs/" + jobId + "/groups", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lines: positions.map(function (value) { return { position: value }; }),
        keep_invalid: keepInvalid(),
      }),
    }).then(applyState).catch(fail);
  }

  document.getElementById("groups-apply").addEventListener("click", function () {
    saveGroups();
  });

  document.getElementById("groups-clear").addEventListener("click", function () {
    if (!window.confirm("撤销全部分组线？")) return;
    request("/api/jobs/" + jobId + "/groups", { method: "DELETE" })
      .then(applyState).catch(fail);
  });

  // ------------------------------------------------------- expanded preview

  function updateExpandedPreview() {
    const tbody = document.querySelector("#expanded-table tbody");
    tbody.innerHTML = "";
    (state.expansion || []).forEach(function (row) {
      const tr = document.createElement("tr");
      tr.className = "row-" + row.export_status;
      function add(text, className) {
        const td = document.createElement("td");
        if (className) td.className = className;
        td.textContent = text;
        tr.appendChild(td);
      }
      add(row.row, "muted");
      add(row.group, "muted");
      add(row.name);
      add(row.spec);
      add(row.price);
      add(row.name_source || "", "muted");
      add(row.spec_source || "", "muted");
      const status = document.createElement("td");
      if (row.export_status === "export") {
        status.innerHTML = '<span class="tag ok">已确定</span>';
      } else if (row.export_status === "skip" && row.status === "determined") {
        status.innerHTML = '<span class="tag skip">批发价为空，不导出</span>';
      } else if (row.export_status === "skip") {
        status.innerHTML = '<span class="tag skip">空白行，不导出</span>';
      } else {
        const tag = document.createElement("span");
        tag.className = "tag review";
        tag.textContent = "待核";
        status.appendChild(tag);
        const list = document.createElement("ul");
        list.className = "reasons";
        row.reasons.forEach(function (reason) {
          const item = document.createElement("li");
          item.textContent = reason.reason + "：涉及第 " +
            reason.rows.join("、") + " 行" +
            (reason.boxes ? "（框 " + reason.boxes.join("、") + "）" : "");
          list.appendChild(item);
        });
        status.appendChild(list);
      }
      tr.appendChild(status);
      tbody.appendChild(tr);
    });
    document.getElementById("expanded-empty").hidden =
      (state.expansion || []).length > 0;
  }

  function updateDownloadLink() {
    const link = document.getElementById("download-expanded-link");
    if (!link) return;
    if (state.can_export_expanded) {
      link.className = "button primary";
      link.setAttribute("href", "/jobs/" + jobId + "/export-expanded.csv");
      link.removeAttribute("aria-disabled");
      link.removeAttribute("title");
    } else {
      link.className = "button disabled";
      link.removeAttribute("href");
      link.setAttribute("aria-disabled", "true");
      link.setAttribute("title", state.expand_block || "需要先确认分组线");
    }
  }

  // ---------------------------------------------------------------- state
  function applyState(next) {
    state = next;
    revoked = [];
    groupDraft = savedPositions();
    render();
    renderGroups();
    updateExpandedPreview();
    updateDownloadLink();
  }

  const initial = document.getElementById("initial-state");
  let parsed = null;
  try {
    parsed = JSON.parse(initial.textContent);
  } catch (e) {
    parsed = null;
  }
  if (parsed) {
    applyState(parsed);
  } else {
    request("/api/jobs/" + jobId).then(applyState).catch(fail);
  }
})();
