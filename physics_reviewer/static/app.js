const form = document.querySelector("#upload-form");
const input = document.querySelector("#pdf-input");
const dropzone = document.querySelector("#dropzone");
const submitButton = document.querySelector("#submit-button");
const fileMeta = document.querySelector("#file-meta");
const statusText = document.querySelector("#status-text");
const statusDot = document.querySelector("#status-dot");
const progressBar = document.querySelector("#progress-bar");
const emptyState = document.querySelector("#empty-state");
const resultView = document.querySelector("#result-view");
const taskPanel = document.querySelector("#task-panel");
const taskList = document.querySelector("#task-list");
const exportActions = document.querySelector("#export-actions");
const exportCsv = document.querySelector("#export-csv");
const exportXlsx = document.querySelector("#export-xlsx");

const requestedBatchId = new URLSearchParams(window.location.search).get("batch");
const storedBatchId = window.localStorage.getItem("physics-reviewer:last-batch-id");
let currentBatchId = requestedBatchId || storedBatchId || null;
if (requestedBatchId) {
  window.localStorage.setItem("physics-reviewer:last-batch-id", requestedBatchId);
}
let pollTimer = null;

const scoreLabels = {
  novelty: "Novelty",
  physics_correctness: "Physics Correctness",
  method_rigor: "Method Rigor",
  reproducibility: "Reproducibility",
  citation_quality: "Citation Quality",
  writing_quality: "Writing Quality",
};

function setStatus(text, kind = "", progress = 0) {
  statusText.textContent = text;
  statusDot.className = `status-dot ${kind}`.trim();
  progressBar.style.width = `${progress}%`;
}

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

function selectedFiles() {
  return Array.from(input.files || []);
}

function setFiles(files) {
  const pdfs = Array.from(files || []);
  if (!pdfs.length) {
    fileMeta.textContent = "No files selected";
    submitButton.disabled = true;
    return;
  }

  const invalid = pdfs.find(
    (file) => file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf"),
  );
  if (invalid) {
    fileMeta.innerHTML = '<span class="error-message">Only PDF files are supported.</span>';
    submitButton.disabled = true;
    return;
  }

  const totalBytes = pdfs.reduce((sum, file) => sum + file.size, 0);
  fileMeta.textContent = `${pdfs.length} file(s) selected · ${formatBytes(totalBytes)}`;
  submitButton.disabled = false;
  setStatus("Ready to upload", "", 0);
}

function renderList(selector, items) {
  const node = document.querySelector(selector);
  if (!node) {
    return;
  }
  node.innerHTML = "";
  const values = Array.isArray(items) && items.length ? items : ["None"];
  values.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item;
    node.appendChild(li);
  });
}

function renderScores(scores) {
  const grid = document.querySelector("#score-grid");
  if (!grid) {
    return;
  }
  grid.innerHTML = "";
  Object.entries(scoreLabels).forEach(([key, label]) => {
    const item = document.createElement("div");
    item.className = "score-item";
    item.innerHTML = `<span>${label}</span><strong>${scores[key] ?? "--"}/5</strong>`;
    grid.appendChild(item);
  });
}

function renderAgents(findings) {
  const list = document.querySelector("#agent-list");
  if (!list) {
    return;
  }
  list.innerHTML = "";
  (findings || []).forEach((finding) => {
    const item = document.createElement("div");
    item.className = "agent-item";
    const title = document.createElement("div");
    title.className = "agent-title";
    title.innerHTML = `<strong>${finding.agent}</strong><span class="badge ${finding.status}">${finding.status}</span>`;
    item.appendChild(title);

    const ul = document.createElement("ul");
    const rows = Array.isArray(finding.findings) && finding.findings.length ? finding.findings : ["No findings"];
    rows.forEach((row) => {
      const li = document.createElement("li");
      li.textContent = row;
      ul.appendChild(li);
    });
    item.appendChild(ul);
    list.appendChild(item);
  });
}

function isSafeHttpUrl(url) {
  try {
    const parsed = new URL(url, window.location.href);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

function renderLiterature(papers) {
  const list = document.querySelector("#literature-list");
  if (!list) {
    return;
  }
  list.innerHTML = "";
  if (!Array.isArray(papers) || papers.length === 0) {
    const empty = document.createElement("div");
    empty.className = "literature-item";
    empty.textContent = "No external similar papers found.";
    list.appendChild(empty);
    return;
  }

  papers.forEach((paper) => {
    const item = document.createElement("div");
    item.className = "literature-item";
    const titleUrl = typeof paper.url === "string" && isSafeHttpUrl(paper.url) ? paper.url : null;
    const titleEl = document.createElement(titleUrl ? "a" : "strong");
    titleEl.textContent = paper.title || "Untitled paper";
    if (titleUrl) {
      titleEl.href = titleUrl;
      titleEl.target = "_blank";
      titleEl.rel = "noreferrer";
    }
    item.appendChild(titleEl);

    const authors = Array.isArray(paper.authors) ? paper.authors.slice(0, 4).join(", ") : "";
    const meta = document.createElement("div");
    meta.className = "literature-meta";
    meta.textContent = `${paper.source || "unknown"} · ${paper.year || "n.d."} · ${authors || "unknown authors"}`;
    item.appendChild(meta);

    const abstractEl = document.createElement("p");
    abstractEl.textContent = paper.abstract
      ? `${paper.abstract.slice(0, 360)}${paper.abstract.length > 360 ? "..." : ""}`
      : "No abstract available.";
    item.appendChild(abstractEl);
    list.appendChild(item);
  });
}

function renderResult(data) {
  const report = data?.report;
  if (!report?.scores) {
    showError("The review completed, but the returned report is incomplete.");
    return;
  }

  // Reveal the report before optional sections render so one malformed section
  // cannot leave a successfully generated review hidden behind the empty state.
  emptyState.classList.add("hidden");
  resultView.classList.remove("hidden");
  document.querySelector("#paper-title").textContent = report.title || "Untitled paper";
  document.querySelector("#overall-score").textContent = report.scores.overall_score ?? "--";
  document.querySelector("#summary").textContent = report.summary || "No summary.";
  renderScores(report.scores);
  renderList("#strengths", report.strengths);
  renderList("#weaknesses", report.weaknesses);
  renderList("#required-checks", report.required_checks);
  renderList("#uncertainty-notes", report.uncertainty_notes);
  renderLiterature(data.literature || []);
  renderAgents(data.findings || []);
}

async function submitBatch(files) {
  const body = new FormData();
  files.forEach((file) => body.append("files", file));
  setStatus("Uploading batch", "running", 10);
  submitButton.disabled = true;

  const response = await fetch("/batches/pdf", { method: "POST", body });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.detail || "Upload failed");
  }

  currentBatchId = data.batch_id;
  window.localStorage.setItem("physics-reviewer:last-batch-id", currentBatchId);
  exportCsv.href = `/batches/${currentBatchId}/export?format=csv`;
  exportXlsx.href = `/batches/${currentBatchId}/export?format=xlsx`;
  exportActions.hidden = false;
  taskPanel.classList.remove("hidden");
  setStatus("Batch queued", "running", 20);
  await pollBatch();
  pollTimer = window.setInterval(pollBatch, 2500);
}

async function pollBatch() {
  if (!currentBatchId) {
    return;
  }

  const response = await fetch(`/batches/${currentBatchId}`);
  const batch = await response.json();
  if (!response.ok) {
    throw new Error(batch.detail || "Failed to fetch batch status");
  }

  renderTasks(batch.tasks);
  const done = batch.succeeded + batch.failed;
  const progress = batch.total ? Math.round((done / batch.total) * 100) : 0;
  const label = `${done}/${batch.total} done · ${batch.running} running · ${batch.failed} failed`;
  setStatus(label, batch.failed ? "error" : done === batch.total ? "done" : "running", progress);

  const firstSucceeded = batch.tasks.find((task) => task.status === "succeeded" && task.result);
  if (firstSucceeded && resultView.classList.contains("hidden")) {
    renderResult(firstSucceeded.result);
  }

  if (done === batch.total && pollTimer) {
    window.clearInterval(pollTimer);
    pollTimer = null;
    submitButton.disabled = false;
  }
  return batch;
}

function renderTasks(tasks) {
  taskList.innerHTML = "";
  tasks.forEach((task) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `task-item ${task.status}`;
    item.innerHTML = `<span>${task.filename || task.title || task.task_id}</span><strong>${task.status}</strong>`;
    item.addEventListener("click", () => {
      if (task.result) {
        renderResult(task.result);
      } else if (task.error) {
        showError(task.error);
      }
    });
    taskList.appendChild(item);
  });
}

function showError(message) {
  emptyState.classList.remove("hidden");
  resultView.classList.add("hidden");
  emptyState.innerHTML = `<h2>Review failed</h2><p class="error-message"></p>`;
  emptyState.querySelector("p").textContent = message;
}

input.addEventListener("change", () => setFiles(input.files));

dropzone.addEventListener("dragover", (event) => {
  event.preventDefault();
  dropzone.classList.add("dragover");
});

dropzone.addEventListener("dragleave", () => {
  dropzone.classList.remove("dragover");
});

dropzone.addEventListener("drop", (event) => {
  event.preventDefault();
  dropzone.classList.remove("dragover");
  input.files = event.dataTransfer.files;
  setFiles(event.dataTransfer.files);
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = selectedFiles();
  if (!files.length) {
    return;
  }

  try {
    if (pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = null;
    }
    await submitBatch(files);
  } catch (error) {
    setStatus("Review failed", "error", 100);
    showError(error.message);
    submitButton.disabled = false;
  }
});

async function restoreLastBatch() {
  if (!currentBatchId) {
    return;
  }

  exportCsv.href = `/batches/${currentBatchId}/export?format=csv`;
  exportXlsx.href = `/batches/${currentBatchId}/export?format=xlsx`;
  exportActions.hidden = false;
  taskPanel.classList.remove("hidden");

  try {
    const batch = await pollBatch();
    if (batch && batch.succeeded + batch.failed < batch.total) {
      pollTimer = window.setInterval(pollBatch, 2500);
    }
  } catch (error) {
    window.localStorage.removeItem("physics-reviewer:last-batch-id");
    currentBatchId = null;
    taskPanel.classList.add("hidden");
    exportActions.hidden = true;
    setStatus("Waiting for upload", "", 0);
  }
}

restoreLastBatch();
