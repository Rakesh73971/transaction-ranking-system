const transactionForm = document.getElementById("transactionForm");
const summaryForm = document.getElementById("summaryForm");
const loadSummaryButton = document.getElementById("loadSummary");
const refreshRankingButton = document.getElementById("refreshRanking");
const regenKeyButton = document.getElementById("regenKey");
const transactionResult = document.getElementById("transactionResult");
const summaryResult = document.getElementById("summaryResult");
const rankingTable = document.getElementById("rankingTable");
const idempotencyKeyInput = document.getElementById("idempotencyKey");
const summaryUserIdInput = document.getElementById("summaryUserId");

function pretty(value) {
  return JSON.stringify(value, null, 2);
}

function setText(node, value) {
  node.textContent = typeof value === "string" ? value : pretty(value);
}

function newKey() {
  return crypto.randomUUID();
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
  try {
    await refreshSummary(summaryUserIdInput.value.trim());
  } catch (error) {
    setText(summaryResult, { error: error.message });
  }
});

refreshRankingButton.addEventListener("click", async () => {
  try {
    await refreshRanking();
  } catch (error) {
    rankingTable.innerHTML = `<pre class="result">${error.message}</pre>`;
  }
});

regenKeyButton.addEventListener("click", () => {
  idempotencyKeyInput.value = newKey();
});

idempotencyKeyInput.value = newKey();

Promise.all([
  refreshSummary(summaryUserIdInput.value.trim()).catch((error) => {
    setText(summaryResult, { error: error.message });
  }),
  refreshRanking().catch((error) => {
    rankingTable.innerHTML = `<pre class="result">${error.message}</pre>`;
  }),
]).catch(() => {});
