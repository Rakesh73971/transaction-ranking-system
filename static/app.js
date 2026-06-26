const transactionForm = document.getElementById("transactionForm");
const summaryForm = document.getElementById("summaryForm");
const loadSummaryButton = document.getElementById("loadSummary");
const refreshRankingButton = document.getElementById("refreshRanking");
const regenKeyButton = document.getElementById("regenKey");
const idempotencyChip = document.getElementById("idempotencyChip");
const atomicChip = document.getElementById("atomicChip");
const fairScoreChip = document.getElementById("fairScoreChip");
const transactionResult = document.getElementById("transactionResult");
const summaryResult = document.getElementById("summaryResult");
const rankingTable = document.getElementById("rankingTable");
const idempotencyKeyInput = document.getElementById("idempotencyKey");
const summaryUserIdInput = document.getElementById("summaryUserId");
const lastUpdated = document.getElementById("lastUpdated");
const summaryPanel = summaryForm.closest(".panel");
const rankingPanel = rankingTable.closest(".panel");
const transactionPanel = transactionForm.closest(".panel");

function pretty(value) {
  return JSON.stringify(value, null, 2);
}

function setText(node, value) {
  node.textContent = typeof value === "string" ? value : pretty(value);
}

function newKey() {
  return crypto.randomUUID();
}

function scrollToPanel(panel) {
  panel?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function markUpdated(message) {
  lastUpdated.textContent = message;
}

function setBusy(button, isBusy, label) {
  if (isBusy) {
    button.disabled = true;
    button.dataset.originalLabel = button.textContent;
    button.textContent = label || "Loading...";
    return;
  }

  button.disabled = false;
  if (button.dataset.originalLabel) {
    button.textContent = button.dataset.originalLabel;
    delete button.dataset.originalLabel;
  }
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(options.headers || {}),
    },
    ...options,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = payload.detail || payload.error || response.statusText;
    throw new Error(message);
  }
  return payload;
}

async function refreshSummary(userId) {
  const payload = await requestJson(`/summary/${encodeURIComponent(userId)}`);
  setText(summaryResult, payload);
  return payload;
}

async function refreshRanking() {
  const payload = await requestJson("/ranking?limit=10");
  const rows = payload.ranking || [];
  rankingTable.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Rank</th>
          <th>User</th>
          <th>Score</th>
          <th>Points</th>
          <th>Tx</th>
          <th>Active Days</th>
          <th>Last Activity</th>
        </tr>
      </thead>
      <tbody>
        ${rows
          .map(
            (row, index) => `
              <tr>
                <td>${index + 1}</td>
                <td>${row.userId}</td>
                <td>${row.score.toFixed(2)}</td>
                <td>${row.totalPoints}</td>
                <td>${row.transactionCount}</td>
                <td>${row.activeDays}</td>
                <td>${row.lastTransactionAt || "-"}</td>
              </tr>
            `,
          )
          .join("")}
      </tbody>
    </table>
  `;
}

async function refreshDashboard() {
  const userId = summaryUserIdInput.value.trim();
  await Promise.all([
    refreshSummary(userId),
    refreshRanking(),
  ]);
  markUpdated(`Last refreshed at ${new Date().toLocaleTimeString()}`);
}

function ensureIdempotencyKey() {
  if (!idempotencyKeyInput.value.trim()) {
    idempotencyKeyInput.value = newKey();
  }
}

transactionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  ensureIdempotencyKey();
  const body = {
    userId: transactionForm.userId.value.trim(),
    amount: Number(transactionForm.amount.value),
    idempotencyKey: idempotencyKeyInput.value.trim(),
    note: transactionForm.note.value.trim() || null,
  };

  try {
    const payload = await requestJson("/transaction", {
      method: "POST",
      body: JSON.stringify(body),
    });
    setText(transactionResult, payload);
    summaryUserIdInput.value = body.userId;
    await refreshSummary(body.userId);
    await refreshRanking();
    markUpdated(`Last refreshed at ${new Date().toLocaleTimeString()}`);
    idempotencyKeyInput.value = newKey();
  } catch (error) {
    setText(transactionResult, { error: error.message });
  }
});

summaryForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await refreshSummary(summaryUserIdInput.value.trim());
  } catch (error) {
    setText(summaryResult, { error: error.message });
  }
});

loadSummaryButton.addEventListener("click", async () => {
  setBusy(loadSummaryButton, true, "Refreshing...");
  try {
    await refreshDashboard();
    scrollToPanel(summaryPanel);
  } catch (error) {
    setText(summaryResult, { error: error.message });
    markUpdated("Refresh failed");
  } finally {
    setBusy(loadSummaryButton, false);
  }
});

refreshRankingButton.addEventListener("click", async () => {
  setBusy(refreshRankingButton, true, "Refreshing...");
  try {
    await refreshRanking();
    markUpdated(`Ranking refreshed at ${new Date().toLocaleTimeString()}`);
  } catch (error) {
    rankingTable.innerHTML = `<pre class="result">${error.message}</pre>`;
    markUpdated("Ranking refresh failed");
  } finally {
    setBusy(refreshRankingButton, false);
  }
});

regenKeyButton.addEventListener("click", () => {
  idempotencyKeyInput.value = newKey();
});

idempotencyChip.addEventListener("click", () => {
  scrollToPanel(transactionPanel);
  idempotencyKeyInput.focus();
  idempotencyKeyInput.select();
});

atomicChip.addEventListener("click", async () => {
  setBusy(loadSummaryButton, true, "Refreshing...");
  try {
    await refreshDashboard();
    scrollToPanel(summaryPanel);
  } catch (error) {
    setText(summaryResult, { error: error.message });
    markUpdated("Refresh failed");
  } finally {
    setBusy(loadSummaryButton, false);
  }
});

fairScoreChip.addEventListener("click", async () => {
  setBusy(refreshRankingButton, true, "Refreshing...");
  try {
    await refreshRanking();
    scrollToPanel(rankingPanel);
  } catch (error) {
    rankingTable.innerHTML = `<pre class="result">${error.message}</pre>`;
    markUpdated("Ranking refresh failed");
  } finally {
    setBusy(refreshRankingButton, false);
  }
});

idempotencyKeyInput.value = newKey();

Promise.all([
  refreshDashboard().catch((error) => {
    setText(summaryResult, { error: error.message });
    markUpdated("Live data unavailable");
  }),
]).catch(() => {});
