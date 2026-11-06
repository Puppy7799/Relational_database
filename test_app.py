import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import app


class BookstoreApiTests(unittest.TestCase):
    def setUp(self):
        self.original_database = app.DATABASE
        self.temp_dir = tempfile.TemporaryDirectory()
        app.DATABASE = Path(self.temp_dir.name) / "test.db"
        app.initialize_database()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), app.BookstoreHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        app.DATABASE = self.original_database
        self.temp_dir.cleanup()

    def request(self, method, path, payload=None):
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            self.base_url + path,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def create_book(self, stock=5):
        status, response = self.request("POST", "/api/books", {
            "title": "Transaction Testing",
            "isbn": "978-1-0000-0000-1",
            "unit_price": 24,
            "stock": stock,
            "published_year": 2025,
            "authors": ["Casey Reader"],
        })
        self.assertEqual(status, 201)
        return response["id"]

    def create_customer(self):
        status, response = self.request("POST", "/api/customers", {
            "name": "Riley Customer",
            "email": "riley@example.test",
            "phone": "",
        })
        self.assertEqual(status, 201)
        return response["id"]

    def test_book_customer_order_and_cancellation_preserve_stock(self):
        book_id = self.create_book()
        customer_id = self.create_customer()
        status, created = self.request("POST", "/api/orders", {
            "customer_id": customer_id,
            "items": [{"book_id": book_id, "quantity": 2}],
        })
        self.assertEqual(status, 201)

        with app.database() as connection:
            self.assertEqual(
                connection.execute("SELECT stock FROM books WHERE id = ?", (book_id,)).fetchone()[0],
                3,
            )
        status, orders = self.request("GET", "/api/orders")
        self.assertEqual(status, 200)
        order = next(row for row in orders if row["id"] == created["id"])
        self.assertEqual(order["total"], 48)
        self.assertEqual(order["customer_name"], "Riley Customer")

        status, _ = self.request("PUT", f"/api/books/{book_id}", {
            "title": "Transaction Testing",
            "isbn": "978-1-0000-0000-1",
            "unit_price": 30,
            "stock": 3,
            "published_year": 2025,
            "authors": ["Casey Reader"],
        })
        self.assertEqual(status, 200)
        status, orders = self.request("GET", "/api/orders")
        order = next(row for row in orders if row["id"] == created["id"])
        self.assertEqual(order["total"], 48)

        status, _ = self.request("PUT", f"/api/orders/{created['id']}", {"status": "cancelled"})
        self.assertEqual(status, 200)
        with app.database() as connection:
            self.assertEqual(
                connection.execute("SELECT stock FROM books WHERE id = ?", (book_id,)).fetchone()[0],
                5,
            )
        status, _ = self.request("PUT", f"/api/orders/{created['id']}", {"status": "pending"})
        self.assertEqual(status, 400)
        status, _ = self.request("DELETE", f"/api/customers/{customer_id}")
        self.assertEqual(status, 409)
        status, _ = self.request("DELETE", f"/api/orders/{created['id']}")
        self.assertEqual(status, 200)
        status, _ = self.request("DELETE", f"/api/customers/{customer_id}")
        self.assertEqual(status, 200)

    def test_failed_order_rolls_back_and_deleting_open_order_restores_stock(self):
        book_id = self.create_book(stock=1)
        customer_id = self.create_customer()
        status, _ = self.request("POST", "/api/orders", {
            "customer_id": customer_id,
            "items": [{"book_id": book_id, "quantity": 2}],
        })
        self.assertEqual(status, 400)
        with app.database() as connection:
            self.assertEqual(
                connection.execute("SELECT stock FROM books WHERE id = ?", (book_id,)).fetchone()[0],
                1,
            )
            order_count = connection.execute(
                "SELECT COUNT(*) FROM sales_orders WHERE customer_id = ?", (customer_id,)
            ).fetchone()[0]
            self.assertEqual(order_count, 0)

        status, order = self.request("POST", "/api/orders", {
            "customer_id": customer_id,
            "items": [{"book_id": book_id, "quantity": 1}],
        })
        self.assertEqual(status, 201)
        status, _ = self.request("DELETE", f"/api/orders/{order['id']}")
        self.assertEqual(status, 200)
        with app.database() as connection:
            self.assertEqual(
                connection.execute("SELECT stock FROM books WHERE id = ?", (book_id,)).fetchone()[0],
                1,
            )

    def test_dashboard_aggregates_and_book_search_include_authors(self):
        book_id = self.create_book()
        customer_id = self.create_customer()
        self.request("POST", "/api/orders", {
            "customer_id": customer_id,
            "items": [{"book_id": book_id, "quantity": 1}],
        })
        status, search_results = self.request("GET", "/api/books?search=Casey")
        self.assertEqual(status, 200)
        self.assertEqual(search_results[0]["id"], book_id)
        self.assertEqual(search_results[0]["authors"], "Casey Reader")
        status, dashboard = self.request("GET", "/api/dashboard")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(dashboard["stats"]["revenue"], 24)


if __name__ == "__main__":
    unittest.main()
