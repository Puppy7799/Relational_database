import json
import mimetypes
import sqlite3
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "bookstore.db"
STATIC = ROOT / "static"


def connect():
    connection = sqlite3.connect(DATABASE, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


@contextmanager
def database():
    connection = connect()
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database():
    with database() as connection:
        connection.executescript((ROOT / "schema.sql").read_text(encoding="utf-8"))
        count = connection.execute("SELECT COUNT(*) FROM books").fetchone()[0]
        if count == 0:
            seed_database(connection)


def seed_database(connection):
    authors = ["Mina Patel", "Jonah Reed", "Sofia Chen", "Elliot James"]
    connection.executemany("INSERT INTO authors (name) VALUES (?)", [(name,) for name in authors])
    author_ids = {
        row["name"]: row["id"]
        for row in connection.execute("SELECT id, name FROM authors")
    }
    books = [
        ("978-1-4028-9462-6", "The Quiet Algorithm", 24.00, 18, 2023, "Mina Patel"),
        ("978-1-4028-9463-3", "Small Hours, Big Ideas", 18.50, 7, 2022, "Jonah Reed"),
        ("978-1-4028-9464-0", "A Field Guide to Elsewhere", 32.00, 4, 2024, "Sofia Chen"),
        ("978-1-4028-9465-7", "The Shape of Things", 21.00, 12, 2021, "Elliot James"),
        ("978-1-4028-9466-4", "Notes from the Blue Room", 16.75, 3, 2020, "Mina Patel"),
    ]
    for isbn, title, price, stock, year, author in books:
        cursor = connection.execute(
            """INSERT INTO books (isbn, title, unit_price, stock, published_year)
               VALUES (?, ?, ?, ?, ?)""",
            (isbn, title, price, stock, year),
        )
        connection.execute(
            "INSERT INTO book_authors (book_id, author_id) VALUES (?, ?)",
            (cursor.lastrowid, author_ids[author]),
        )
    customers = [
        ("Avery Morgan", "avery@example.com", "+1 555 0101"),
        ("Taylor Brooks", "taylor@example.com", "+1 555 0102"),
        ("Jordan Rivera", "jordan@example.com", "+1 555 0103"),
    ]
    connection.executemany(
        "INSERT INTO customers (name, email, phone) VALUES (?, ?, ?)", customers
    )
    customer_ids = {
        row["email"]: row["id"]
        for row in connection.execute("SELECT id, email FROM customers")
    }
    book_ids = {
        row["title"]: row["id"]
        for row in connection.execute("SELECT id, title FROM books")
    }
    sample_orders = [
        ("avery@example.com", "fulfilled", [("The Quiet Algorithm", 2), ("The Shape of Things", 1)]),
        ("taylor@example.com", "pending", [("Small Hours, Big Ideas", 1)]),
        ("jordan@example.com", "fulfilled", [("A Field Guide to Elsewhere", 1), ("Notes from the Blue Room", 2)]),
    ]
    for email, status, items in sample_orders:
        cursor = connection.execute(
            "INSERT INTO sales_orders (customer_id, status) VALUES (?, ?)",
            (customer_ids[email], status),
        )
        for title, quantity in items:
            book_id = book_ids[title]
            price = connection.execute(
                "SELECT unit_price FROM books WHERE id = ?", (book_id,)
            ).fetchone()["unit_price"]
            connection.execute(
                """INSERT INTO order_items (order_id, book_id, quantity, unit_price)
                   VALUES (?, ?, ?, ?)""",
                (cursor.lastrowid, book_id, quantity, price),
            )
            connection.execute(
                "UPDATE books SET stock = stock - ? WHERE id = ?",
                (quantity, book_id),
            )


def parse_decimal(value, field):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field} must be a valid number.")
    if not amount.is_finite() or amount < 0:
        raise ValueError(f"{field} must be zero or greater.")
    return float(amount.quantize(Decimal("0.01")))


def require_text(data, field, label=None):
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label or field} is required.")
    return value.strip()


def book_payload(data):
    title = require_text(data, "title", "Title")
    isbn = data.get("isbn")
    isbn = isbn.strip() if isinstance(isbn, str) and isbn.strip() else None
    stock = data.get("stock", 0)
    year = data.get("published_year")
    if isinstance(stock, bool) or not isinstance(stock, int) or stock < 0:
        raise ValueError("Stock must be a whole number of zero or greater.")
    if year in ("", None):
        year = None
    elif isinstance(year, bool) or not isinstance(year, int) or not 1000 <= year <= 2100:
        raise ValueError("Published year must be between 1000 and 2100.")
    authors = data.get("authors", [])
    if isinstance(authors, str):
        authors = [name.strip() for name in authors.split(",")]
    if not isinstance(authors, list) or any(not isinstance(name, str) for name in authors):
        raise ValueError("Authors must be a list of names.")
    authors = list(dict.fromkeys(name.strip() for name in authors if name.strip()))
    return isbn, title, parse_decimal(data.get("unit_price"), "Price"), stock, year, authors


def replace_book_authors(connection, book_id, author_names):
    connection.execute("DELETE FROM book_authors WHERE book_id = ?", (book_id,))
    for name in author_names:
        connection.execute("INSERT OR IGNORE INTO authors (name) VALUES (?)", (name,))
        author = connection.execute(
            "SELECT id FROM authors WHERE name = ? COLLATE NOCASE", (name,)
        ).fetchone()
        connection.execute(
            "INSERT INTO book_authors (book_id, author_id) VALUES (?, ?)",
            (book_id, author["id"]),
        )


class BookstoreHandler(BaseHTTPRequestHandler):
    server_version = "BookstoreDemo/1.0"

    def send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid request body.")
        if length <= 0 or length > 1_000_000:
            raise ValueError("Request body must be between 1 byte and 1 MB.")
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("Request body must contain valid JSON.")
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def do_GET(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/dashboard":
                self.dashboard()
            elif parsed.path == "/api/books":
                self.books(parse_qs(parsed.query).get("search", [""])[0])
            elif parsed.path == "/api/customers":
                self.customers()
            elif parsed.path == "/api/orders":
                self.orders()
            elif parsed.path.startswith("/api/"):
                self.send_json({"error": "API route not found."}, 404)
            else:
                self.serve_static(parsed.path)
        except sqlite3.Error as error:
            self.send_json({"error": f"Database error: {error}"}, 500)

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            data = self.read_json()
            if parsed.path == "/api/books":
                self.create_book(data)
            elif parsed.path == "/api/customers":
                self.create_customer(data)
            elif parsed.path == "/api/orders":
                self.create_order(data)
            else:
                self.send_json({"error": "API route not found."}, 404)
        except ValueError as error:
            self.send_json({"error": str(error)}, 400)
        except sqlite3.IntegrityError as error:
            self.send_json({"error": self.integrity_message(error)}, 409)
        except sqlite3.Error as error:
            self.send_json({"error": f"Database error: {error}"}, 500)

    def do_PUT(self):
        parsed = urlparse(self.path)
        parts = parsed.path.strip("/").split("/")
        try:
            data = self.read_json()
            if len(parts) == 3 and parts[:2] == ["api", "books"]:
                self.update_book(parts[2], data)
            elif len(parts) == 3 and parts[:2] == ["api", "customers"]:
                self.update_customer(parts[2], data)
            elif len(parts) == 3 and parts[:2] == ["api", "orders"]:
                self.update_order(parts[2], data)
            else:
                self.send_json({"error": "API route not found."}, 404)
        except ValueError as error:
            self.send_json({"error": str(error)}, 400)
        except sqlite3.IntegrityError as error:
            self.send_json({"error": self.integrity_message(error)}, 409)
        except sqlite3.Error as error:
            self.send_json({"error": f"Database error: {error}"}, 500)

    def do_DELETE(self):
        parts = urlparse(self.path).path.strip("/").split("/")
        try:
            if len(parts) != 3 or parts[0] != "api" or parts[1] not in ("books", "customers", "orders"):
                self.send_json({"error": "API route not found."}, 404)
                return
            entity_id = self.parse_id(parts[2])
            with database() as connection:
                if parts[1] == "orders":
                    connection.execute("BEGIN IMMEDIATE")
                    order = connection.execute(
                        "SELECT status FROM sales_orders WHERE id = ?", (entity_id,)
                    ).fetchone()
                    if order is None:
                        self.send_json({"error": "Record not found."}, 404)
                        return
                    if order["status"] != "cancelled":
                        connection.execute(
                            """UPDATE books SET stock = stock + (
                                   SELECT quantity FROM order_items
                                    WHERE order_id = ? AND book_id = books.id
                               ) WHERE id IN (SELECT book_id FROM order_items WHERE order_id = ?)""",
                            (entity_id, entity_id),
                        )
                    cursor = connection.execute(
                        "DELETE FROM sales_orders WHERE id = ?", (entity_id,)
                    )
                else:
                    table = "books" if parts[1] == "books" else "customers"
                    cursor = connection.execute(
                        f"DELETE FROM {table} WHERE id = ?", (entity_id,)
                    )
                if cursor.rowcount == 0:
                    self.send_json({"error": "Record not found."}, 404)
                    return
            self.send_json({"ok": True})
        except ValueError as error:
            self.send_json({"error": str(error)}, 400)
        except sqlite3.IntegrityError as error:
            self.send_json({"error": self.integrity_message(error)}, 409)
        except sqlite3.Error as error:
            self.send_json({"error": f"Database error: {error}"}, 500)

    @staticmethod
    def parse_id(value):
        try:
            result = int(value)
        except ValueError:
            raise ValueError("Record ID must be a positive integer.")
        if result <= 0:
            raise ValueError("Record ID must be a positive integer.")
        return result

    @staticmethod
    def integrity_message(error):
        message = str(error)
        if "UNIQUE constraint failed" in message:
            return "That ISBN or email address is already in use."
        if "FOREIGN KEY constraint failed" in message:
            return "This record is linked to other records and cannot be deleted."
        if "CHECK constraint failed" in message:
            return "One or more values do not meet the database constraints."
        return f"Database constraint error: {message}"

    def serve_static(self, request_path):
        allowed = {
            "/": "index.html",
            "/index.html": "index.html",
            "/styles.css": "styles.css",
            "/app.js": "app.js",
        }
        filename = allowed.get(request_path)
        if filename is None:
            self.send_json({"error": "Page not found."}, 404)
            return
        content = (STATIC / filename).read_bytes()
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def dashboard(self):
        with database() as connection:
            stats = connection.execute(
                """SELECT
                       (SELECT COUNT(*) FROM books) AS book_count,
                       (SELECT COUNT(*) FROM customers) AS customer_count,
                       (SELECT COUNT(*) FROM sales_orders WHERE status != 'cancelled') AS order_count,
                       (SELECT COALESCE(SUM(oi.quantity * oi.unit_price), 0)
                          FROM order_items oi JOIN sales_orders so ON so.id = oi.order_id
                         WHERE so.status != 'cancelled') AS revenue,
                       (SELECT COALESCE(SUM(stock * unit_price), 0) FROM books) AS inventory_value,
                       (SELECT COUNT(*) FROM books WHERE stock <= 5) AS low_stock_count"""
            ).fetchone()
            recent = connection.execute(
                """SELECT so.id, so.order_date, so.status, c.name AS customer_name,
                          COALESCE(SUM(oi.quantity * oi.unit_price), 0) AS total
                     FROM sales_orders so
                     JOIN customers c ON c.id = so.customer_id
                     LEFT JOIN order_items oi ON oi.order_id = so.id
                    GROUP BY so.id
                    ORDER BY so.order_date DESC, so.id DESC LIMIT 6"""
            ).fetchall()
            bestsellers = connection.execute(
                """SELECT b.title, SUM(oi.quantity) AS units_sold
                     FROM books b
                     JOIN order_items oi ON oi.book_id = b.id
                     JOIN sales_orders so ON so.id = oi.order_id
                    WHERE so.status != 'cancelled'
                    GROUP BY b.id
                    ORDER BY units_sold DESC, b.title LIMIT 5"""
            ).fetchall()
        self.send_json({
            "stats": dict(stats),
            "recent_orders": [dict(row) for row in recent],
            "bestsellers": [dict(row) for row in bestsellers],
        })

    def books(self, search):
        with database() as connection:
            rows = connection.execute(
                """SELECT b.id, b.isbn, b.title, b.unit_price, b.stock, b.published_year,
                          COALESCE(GROUP_CONCAT(a.name, ', '), '') AS authors,
                          (SELECT COALESCE(SUM(oi.quantity), 0)
                             FROM order_items oi
                             JOIN sales_orders so ON so.id = oi.order_id
                            WHERE oi.book_id = b.id AND so.status != 'cancelled') AS units_sold
                     FROM books b
                     LEFT JOIN book_authors ba ON ba.book_id = b.id
                     LEFT JOIN authors a ON a.id = ba.author_id
                    WHERE b.title LIKE ? OR b.isbn LIKE ? OR a.name LIKE ?
                    GROUP BY b.id ORDER BY b.title COLLATE NOCASE""",
                (f"%{search}%", f"%{search}%", f"%{search}%"),
            ).fetchall()
        self.send_json([dict(row) for row in rows])

    def customers(self):
        with database() as connection:
            rows = connection.execute(
                """SELECT c.id, c.name, c.email, c.phone, c.created_at,
                          COUNT(DISTINCT so.id) AS order_count,
                          COALESCE(SUM(CASE WHEN so.status != 'cancelled'
                                            THEN oi.quantity * oi.unit_price ELSE 0 END), 0) AS lifetime_value
                     FROM customers c
                     LEFT JOIN sales_orders so ON so.customer_id = c.id
                     LEFT JOIN order_items oi ON oi.order_id = so.id
                    GROUP BY c.id ORDER BY c.name COLLATE NOCASE"""
            ).fetchall()
        self.send_json([dict(row) for row in rows])

    def orders(self):
        with database() as connection:
            rows = connection.execute(
                """SELECT so.id, so.order_date, so.status, c.id AS customer_id,
                          c.name AS customer_name, c.email AS customer_email,
                          COUNT(oi.book_id) AS line_count,
                          COALESCE(SUM(oi.quantity * oi.unit_price), 0) AS total,
                          GROUP_CONCAT(b.title || ' x' || oi.quantity, ' | ') AS items
                     FROM sales_orders so
                     JOIN customers c ON c.id = so.customer_id
                     LEFT JOIN order_items oi ON oi.order_id = so.id
                     LEFT JOIN books b ON b.id = oi.book_id
                    GROUP BY so.id
                    ORDER BY so.order_date DESC, so.id DESC"""
            ).fetchall()
        self.send_json([dict(row) for row in rows])

    def create_book(self, data):
        isbn, title, price, stock, year, authors = book_payload(data)
        with database() as connection:
            cursor = connection.execute(
                """INSERT INTO books (isbn, title, unit_price, stock, published_year)
                   VALUES (?, ?, ?, ?, ?)""",
                (isbn, title, price, stock, year),
            )
            replace_book_authors(connection, cursor.lastrowid, authors)
            book_id = cursor.lastrowid
        self.send_json({"id": book_id}, 201)

    def update_book(self, raw_id, data):
        book_id = self.parse_id(raw_id)
        isbn, title, price, stock, year, authors = book_payload(data)
        with database() as connection:
            cursor = connection.execute(
                """UPDATE books SET isbn = ?, title = ?, unit_price = ?, stock = ?, published_year = ?
                   WHERE id = ?""",
                (isbn, title, price, stock, year, book_id),
            )
            if cursor.rowcount == 0:
                self.send_json({"error": "Book not found."}, 404)
                return
            replace_book_authors(connection, book_id, authors)
        self.send_json({"ok": True})

    def create_customer(self, data):
        name = require_text(data, "name", "Name")
        email = require_text(data, "email", "Email")
        if "@" not in email:
            raise ValueError("Enter a valid email address.")
        phone = data.get("phone")
        if phone is not None and not isinstance(phone, str):
            raise ValueError("Phone must be text.")
        with database() as connection:
            cursor = connection.execute(
                "INSERT INTO customers (name, email, phone) VALUES (?, ?, ?)",
                (name, email, phone.strip() if phone else None),
            )
            customer_id = cursor.lastrowid
        self.send_json({"id": customer_id}, 201)

    def update_customer(self, raw_id, data):
        customer_id = self.parse_id(raw_id)
        name = require_text(data, "name", "Name")
        email = require_text(data, "email", "Email")
        if "@" not in email:
            raise ValueError("Enter a valid email address.")
        phone = data.get("phone")
        if phone is not None and not isinstance(phone, str):
            raise ValueError("Phone must be text.")
        with database() as connection:
            cursor = connection.execute(
                "UPDATE customers SET name = ?, email = ?, phone = ? WHERE id = ?",
                (name, email, phone.strip() if phone else None, customer_id),
            )
            if cursor.rowcount == 0:
                self.send_json({"error": "Customer not found."}, 404)
                return
        self.send_json({"ok": True})

    def create_order(self, data):
        customer_id = self.parse_id(str(data.get("customer_id", "")))
        items = data.get("items")
        if not isinstance(items, list) or not items:
            raise ValueError("Add at least one book to the order.")
        quantities = {}
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Each order item must include a book and quantity.")
            book_id = self.parse_id(str(item.get("book_id", "")))
            quantity = item.get("quantity")
            if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
                raise ValueError("Order quantities must be positive whole numbers.")
            quantities[book_id] = quantities.get(book_id, 0) + quantity
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute(
                "SELECT 1 FROM customers WHERE id = ?", (customer_id,)
            ).fetchone() is None:
                raise ValueError("Choose an existing customer.")
            priced_items = []
            for book_id, quantity in quantities.items():
                book = connection.execute(
                    "SELECT unit_price, stock FROM books WHERE id = ?", (book_id,)
                ).fetchone()
                if book is None:
                    raise ValueError("One of the selected books no longer exists.")
                if book["stock"] < quantity:
                    raise ValueError(f"Not enough stock available for book #{book_id}.")
                priced_items.append((book_id, quantity, book["unit_price"]))
            order_cursor = connection.execute(
                "INSERT INTO sales_orders (customer_id) VALUES (?)", (customer_id,)
            )
            order_id = order_cursor.lastrowid
            for book_id, quantity, price in priced_items:
                connection.execute(
                    """INSERT INTO order_items (order_id, book_id, quantity, unit_price)
                       VALUES (?, ?, ?, ?)""",
                    (order_id, book_id, quantity, price),
                )
                connection.execute(
                    "UPDATE books SET stock = stock - ? WHERE id = ?",
                    (quantity, book_id),
                )
        self.send_json({"id": order_id}, 201)

    def update_order(self, raw_id, data):
        order_id = self.parse_id(raw_id)
        status = data.get("status")
        if status not in ("pending", "fulfilled", "cancelled"):
            raise ValueError("Status must be pending, fulfilled, or cancelled.")
        with database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            order = connection.execute(
                "SELECT status FROM sales_orders WHERE id = ?", (order_id,)
            ).fetchone()
            if order is None:
                self.send_json({"error": "Order not found."}, 404)
                return
            if order["status"] == "cancelled" and status != "cancelled":
                raise ValueError("Cancelled orders cannot be reopened.")
            if order["status"] == status:
                self.send_json({"ok": True})
                return
            if status == "cancelled":
                connection.execute(
                    """UPDATE books SET stock = stock + (
                           SELECT quantity FROM order_items
                            WHERE order_id = ? AND book_id = books.id
                       ) WHERE id IN (SELECT book_id FROM order_items WHERE order_id = ?)""",
                    (order_id, order_id),
                )
            connection.execute(
                "UPDATE sales_orders SET status = ? WHERE id = ?", (status, order_id)
            )
        self.send_json({"ok": True})

    def log_message(self, format_string, *args):
        print(f"{self.address_string()} - {format_string % args}")


if __name__ == "__main__":
    initialize_database()
    server = ThreadingHTTPServer(("127.0.0.1", 8000), BookstoreHandler)
    print("Bookstore dashboard running at http://127.0.0.1:8000")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
    finally:
        server.server_close()
