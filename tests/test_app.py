import os
import unittest
import threading
from pathlib import Path
from fastapi.testclient import TestClient

# Import app modules
from app import store
from app.main import app

# Set a separate test database file
TEST_DB_PATH = store.ROOT_DIR / "data" / "test_ledger.db"

class TestTransactionLedger(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Override DB_PATH in the store module BEFORE FastAPI initialization/requests
        store.DB_PATH = TEST_DB_PATH
        if TEST_DB_PATH.exists():
            try:
                TEST_DB_PATH.unlink()
            except OSError:
                pass
        
        # Manually initialize the test database
        store.init_db()
        
        # Setup FastAPI TestClient
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        # Clean up test database file
        if TEST_DB_PATH.exists():
            try:
                TEST_DB_PATH.unlink()
            except OSError:
                pass

    def setUp(self):
        # Clear tables before each test to have a predictable environment
        with store.get_connection() as conn:
            conn.execute("DELETE FROM transactions")
            conn.execute("DELETE FROM user_activity_days")
            conn.execute("DELETE FROM users")

    def test_home_page(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])

    def test_valid_transaction(self):
        payload = {
            "userId": "test-alice",
            "amount": 150,
            "idempotencyKey": "key-alice-101",
            "note": "Initial test deposit"
        }
        response = self.client.post("/transaction", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["message"], "Transaction recorded")
        self.assertEqual(data["transaction"]["userId"], "test-alice")
        self.assertEqual(data["transaction"]["rawAmount"], 150)
        self.assertEqual(data["transaction"]["effectivePoints"], 100) # Capped at 100
        self.assertEqual(data["transaction"]["note"], "Initial test deposit")
        self.assertIsNotNone(data["transaction"]["createdAt"])

    def test_transaction_validation_errors(self):
        # Invalid User ID (too short)
        payload = {
            "userId": "a",
            "amount": 50,
            "idempotencyKey": "valid-key-1",
        }
        response = self.client.post("/transaction", json=payload)
        self.assertEqual(response.status_code, 422)

        # Invalid User ID (characters not allowed)
        payload = {
            "userId": "alice!!!",
            "amount": 50,
            "idempotencyKey": "valid-key-2",
        }
        response = self.client.post("/transaction", json=payload)
        self.assertEqual(response.status_code, 422)

        # Invalid amount (less than 1)
        payload = {
            "userId": "test-alice",
            "amount": 0,
            "idempotencyKey": "valid-key-3",
        }
        response = self.client.post("/transaction", json=payload)
        self.assertEqual(response.status_code, 422)

        # Invalid amount (greater than 10000)
        payload = {
            "userId": "test-alice",
            "amount": 10001,
            "idempotencyKey": "valid-key-4",
        }
        response = self.client.post("/transaction", json=payload)
        self.assertEqual(response.status_code, 422)

    def test_idempotency_success(self):
        payload = {
            "userId": "test-alice",
            "amount": 250,
            "idempotencyKey": "key-idempotent-200",
            "note": "Repeat test"
        }
        # First call
        response1 = self.client.post("/transaction", json=payload)
        self.assertEqual(response1.status_code, 200)
        tx1 = response1.json()["transaction"]

        # Second call with exact same parameters
        response2 = self.client.post("/transaction", json=payload)
        self.assertEqual(response2.status_code, 200)
        tx2 = response2.json()["transaction"]

        # Assert identical records are returned
        self.assertEqual(tx1["idempotencyKey"], tx2["idempotencyKey"])
        self.assertEqual(tx1["createdAt"], tx2["createdAt"])

    def test_idempotency_conflict(self):
        payload1 = {
            "userId": "test-alice",
            "amount": 100,
            "idempotencyKey": "key-idempotent-conflict",
            "note": "Transaction 1"
        }
        response1 = self.client.post("/transaction", json=payload1)
        self.assertEqual(response1.status_code, 200)

        # Different amount
        payload2 = {
            "userId": "test-alice",
            "amount": 200,
            "idempotencyKey": "key-idempotent-conflict",
            "note": "Transaction 2"
        }
        response2 = self.client.post("/transaction", json=payload2)
        self.assertEqual(response2.status_code, 409)
        self.assertIn("Idempotency key collision", response2.json()["detail"])

        # Different user
        payload3 = {
            "userId": "test-bob",
            "amount": 100,
            "idempotencyKey": "key-idempotent-conflict",
            "note": "Transaction 3"
        }
        response3 = self.client.post("/transaction", json=payload3)
        self.assertEqual(response3.status_code, 409)
        self.assertIn("Idempotency key collision", response3.json()["detail"])

    def test_idempotency_conflict_on_note_change(self):
        payload1 = {
            "userId": "test-alice",
            "amount": 100,
            "idempotencyKey": "key-idempotent-note",
            "note": "first note"
        }
        response1 = self.client.post("/transaction", json=payload1)
        self.assertEqual(response1.status_code, 200)

        payload2 = {
            "userId": "test-alice",
            "amount": 100,
            "idempotencyKey": "key-idempotent-note",
            "note": "changed note"
        }
        response2 = self.client.post("/transaction", json=payload2)
        self.assertEqual(response2.status_code, 409)
        self.assertIn("Idempotency key collision", response2.json()["detail"])

    def test_user_summary_not_found(self):
        response = self.client.get("/summary/non-existent-user")
        self.assertEqual(response.status_code, 404)

    def test_user_summary_and_largest_transaction(self):
        # Insert small transaction
        self.client.post("/transaction", json={
            "userId": "test-alice",
            "amount": 50,
            "idempotencyKey": "alice-k1"
        })

        # Insert larger transaction (beyond cap of 100 points)
        self.client.post("/transaction", json={
            "userId": "test-alice",
            "amount": 750,
            "idempotencyKey": "alice-k2"
        })

        # Load user summary
        response = self.client.get("/summary/test-alice")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        
        self.assertEqual(data["userId"], "test-alice")
        self.assertEqual(data["transactionCount"], 2)
        self.assertEqual(data["totalPoints"], 50 + 100) # 50 + min(750, 100) = 150 points
        
        # Verify that largestTransaction returns the RAW transaction amount (750) rather than point cap (100)
        self.assertEqual(data["largestTransaction"], 750)
        self.assertEqual(len(data["recentTransactions"]), 2)

    def test_ranking_logic(self):
        # We will create two users: one with a high single-transaction score, and one consistent user.
        # User 1: test-whale (1 single transaction of 500 points (effective points 100))
        # Total points = 100
        # Largest transaction raw = 500 (capped at 100 points)
        # Dominance Share = 100 / 100 = 1.0 (100%)
        # Dominance Penalty = max(0, 1.0 - 0.55) * 55 = 24.75 points
        # Consistency Bonus = min(1, 40) * 2.25 = 2.25
        # Activity Bonus = min(1, 30) * 3.75 = 3.75
        # Recency Bonus = 18 points (since immediate)
        # Whale score = 100 + 2.25 + 3.75 + 18 - 24.75 = 99.25
        self.client.post("/transaction", json={
            "userId": "test-whale",
            "amount": 500,
            "idempotencyKey": "whale-key"
        })

        # User 2: test-consistent (3 separate transactions of 50 each (effective points 50 * 3 = 150))
        # Total points = 150
        # Largest transaction raw = 50 (effective points 50)
        # Dominance Share = 50 / 150 = 0.33 (33%)
        # Dominance Penalty = max(0, 0.33 - 0.55) * 55 = 0.0 points
        # Consistency Bonus = min(3, 40) * 2.25 = 6.75
        # Activity Bonus = min(1, 30) * 3.75 = 3.75 (since same day)
        # Recency Bonus = 18 points (since immediate)
        # Consistent score = 150 + 6.75 + 3.75 + 18 - 0 = 178.50
        self.client.post("/transaction", json={
            "userId": "test-consistent",
            "amount": 50,
            "idempotencyKey": "consistent-key-1"
        })
        self.client.post("/transaction", json={
            "userId": "test-consistent",
            "amount": 50,
            "idempotencyKey": "consistent-key-2"
        })
        self.client.post("/transaction", json={
            "userId": "test-consistent",
            "amount": 50,
            "idempotencyKey": "consistent-key-3"
        })

        # Fetch Ranking
        response = self.client.get("/ranking")
        self.assertEqual(response.status_code, 200)
        ranking = response.json()["ranking"]

        # Verify test-consistent is ranked #1 (higher score) despite test-whale's single large amount
        self.assertEqual(ranking[0]["userId"], "test-consistent")
        self.assertEqual(ranking[1]["userId"], "test-whale")
        
        # Verify dominance penalty is calculated for whale but not for consistent user
        self.assertEqual(ranking[1]["components"]["dominancePenalty"], 24.75)
        self.assertEqual(ranking[0]["components"]["dominancePenalty"], 0.0)

    def test_concurrency_writes(self):
        # We will write multiple transactions concurrently using threads
        # and ensure SQLite transactions serialize them without losing data or throwing lock errors.
        num_threads = 10
        errors = []
        
        def run_thread(thread_idx):
            try:
                payload = {
                    "userId": f"concurrent-user",
                    "amount": 10,
                    "idempotencyKey": f"concurrent-key-{thread_idx}",
                    "note": f"Thread {thread_idx}"
                }
                response = self.client.post("/transaction", json=payload)
                if response.status_code != 200:
                    errors.append(f"Thread {thread_idx} failed with {response.status_code}: {response.text}")
            except Exception as e:
                errors.append(f"Thread {thread_idx} raised exception: {str(e)}")

        threads = []
        for i in range(num_threads):
            t = threading.Thread(target=run_thread, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        # Check for errors during execution
        self.assertEqual(errors, [], f"Errors encountered during concurrent writes: {errors}")

        # Check total transaction count and points for concurrent-user
        response = self.client.get("/summary/concurrent-user")
        self.assertEqual(response.status_code, 200)
        summary = response.json()
        self.assertEqual(summary["transactionCount"], num_threads)
        self.assertEqual(summary["totalPoints"], num_threads * 10)

if __name__ == "__main__":
    unittest.main()
