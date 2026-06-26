# Transaction Ledger Demo

Small FastAPI + SQLite project that exposes:

- `POST /transaction`
- `GET /summary/:userId`
- `GET /ranking`

The same app serves a simple frontend at `/` so the full flow is visible in one browser session.

## Run locally

1. Install Python 3.13 or newer.
2. From this folder, run:

```bash
py -3.13 -m pip install -r requirements.txt
py -3.13 -m uvicorn app.main:app --reload
```

3. Open `http://127.0.0.1:8000`

## Deploy On Render

Use the following start command in Render:

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Recommended environment variables:

- `DATABASE_PATH=/var/data/ledger.db`
- `CORS_ORIGINS=https://your-frontend.onrender.com` if you host the frontend on a separate Render service

If you attach a Render persistent disk, point `DATABASE_PATH` to that mounted path so transactions survive restarts and redeploys.

## Data model

SQLite is used for durable, file-based storage.

- `users`
  - One row per user.
  - Stores cumulative stats used by summary and ranking.
- `transactions`
  - One row per transaction.
  - `idempotency_key` is unique, so retried requests do not duplicate work.
- `user_activity_days`
  - Tracks one row per user per calendar day.
  - Used to calculate `activeDays` without rescanning the full transaction history.

## API behavior

### `POST /transaction`

Request body:

```json
{
  "userId": "demo-alice",
  "amount": 45,
  "idempotencyKey": "e8f6fda3-4a9f-4cd0-8e3a-7b3f7d3abf21",
  "note": "optional memo"
}
```

Rules:

- `userId` must be 2-32 characters and may contain letters, numbers, `_`, and `-`.
- `amount` must be between `1` and `10000`.
- `idempotencyKey` is required.
- The same `idempotencyKey` is processed only once.

Behavior:

- The write happens inside a single SQLite transaction.
- The service uses `BEGIN IMMEDIATE` plus a unique constraint on `idempotency_key` to prevent duplicate processing.
- Each transaction contributes `min(amount, 100)` points to ranking. This caps the impact of any single transaction and helps reduce ranking manipulation.

### `GET /summary/:userId`

Returns the stored stats for one user:

- total points
- transaction count
- active days
- largest transaction (raw amount; capped at 100 points for the dominance penalty calculation)
- first and last transaction timestamps
- latest transactions

If the user does not exist, the API returns `404`.

### `GET /ranking`

Returns the current ranking list.

Ranking score uses more than one factor:

- total points
- transaction count
- active days
- recency of last activity
- dominance penalty if one transaction makes up too much of the user's total

Score formula:

```text
score = totalPoints
      + consistencyBonus
      + activityBonus
      + recencyBonus
      - dominancePenalty
```

This means a user cannot win purely by posting one oversized transaction, and steady activity matters.

## Duplicate request prevention

Duplicate requests are prevented with two layers:

1. A unique `idempotency_key` column on the `transactions` table.
2. An explicit database transaction around the insert/update flow.

- If a request is retried with the same key and **identical** transaction parameters, the existing transaction is returned (standard idempotent success).
- If a request is retried with the same key but **different** parameters (different `userId`, `amount`, or `note`), the server returns an HTTP `409 Conflict` error to prevent key collision abuse or parameter modification.

## Concurrency handling

- SQLite handles the durable write.
- The app uses `BEGIN IMMEDIATE` so competing writes are serialized safely.
- User summary fields are updated in the same transaction as the transaction insert.
- Ranking reads from the stored user stats, so it always reflects committed state.

## Demo data

The database seeds a few sample users on the first run so the frontend has something to show immediately.
Delete `data/ledger.db` if you want a fresh start.

## Trade-offs

- SQLite is a good fit for this assignment and keeps the project easy to run.
- For a heavier production system, I would replace it with PostgreSQL and move the ranking calculation into a dedicated read model or cache layer.
- Ranking uses a capped transaction contribution to reduce manipulation, but a production system would likely add rate limits and abuse scoring as well.

## Submission Details

- **Live URL**: [https://transaction-ranking-system-xb5m.onrender.com/](https://transaction-ranking-system-xb5m.onrender.com/)
- **Demo Video Walkthrough**: [Google Drive Link](https://drive.google.com/file/d/1KOxNTi29Mb9WwxiuSgfxVskUC-nyfR2J/view?usp=drive_link)

---

## Security & Implementation Improvements

The frontend was polished to meet security best practices:
1. **DOM-based XSS Mitigation**: Dynamic rendering of user inputs (like `userId` and API errors) in the scoreboard is securely executed using `document.createElement` and `textContent` rather than interpolating strings inside `.innerHTML`.
2. **Synchronized Form Validations**: The HTML input forms utilize browser-native validation matching the exact backend expectations (e.g., regex `pattern="[A-Za-z0-9_-]+"`, character limits, and integer-only amounts).
3. **Locale Datetime Formatting**: Server ISO timestamps are formatted to the browser's local timezone.

## Demo Video Outline

For the required 3-5 minute screen recording, we recommend covering:
1. **Application Overview**: Brief walkthrough of the single-page interface and features.
2. **API Flow demonstration**:
   - Post a transaction with the form (explain how the UI generates unique UUIDs to prevent duplicate submissions).
   - Show how the idempotency key works by resubmitting the form with the same key.
   - Inspect a specific user's summary.
3. **Multi-Factor Scoreboard**: Explain the ranking logic (bonuses for active days, transaction counts, recency, and the dominance penalty for single large transactions).
4. **Concurrency & Data Consistency**: Highlight the use of SQLite's WAL mode and `BEGIN IMMEDIATE` transaction locking to guarantee serializability.

