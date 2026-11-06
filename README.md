# Paper & Pine Bookshop

A small, full-stack bookstore inventory and order-management project. It uses Python's standard-library HTTP server, SQLite, and a responsive vanilla JavaScript UI, so it runs without installing third-party packages.

## Run it

From the project directory:

```powershell
python app.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). The app creates `bookstore.db` and inserts a small sample catalog the first time it runs. The database file is local and can be removed to reset the demo data.

## Relational model

- **Books** and **authors** have a many-to-many relationship through `book_authors`.
- **Customers** place **sales_orders**; each order has one or more **order_items**.
- Each order item stores a price snapshot so later book price changes do not rewrite sales history.
- Foreign keys, unique ISBN/email values, non-negative prices and stock, positive order quantities, and a constrained order status protect data integrity.
- Indexes support title lookup, author-to-book joins, customer order history, order status/date filters, and book sales aggregation.

The schema is in [`schema.sql`](./schema.sql). It is normalized so author and customer details are stored once; relationship tables represent many-to-many links and order line items.

## Included operations

- CRUD for books, authors (through book author assignment), customers, and orders.
- Search books by title, ISBN, or author.
- Joined views for book authors, order/customer summaries, and customer lifetime value.
- Dashboard subqueries and aggregations for net sales, inventory value, low stock, recent orders, and bestsellers.
- Transactions protect order creation, reserve inventory atomically, and restore stock on cancellation or deletion.
- Referential constraints prevent deleting books or customers that still appear in order history.

The API is served by `app.py`; browser assets are in `static/`.

## Test it

```powershell
python -m unittest -v
```

The integration tests use a temporary SQLite database and cover CRUD constraints, joined reporting, price snapshots, failed-order rollback, and stock restoration on cancellation or deletion.
