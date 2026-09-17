"""
seed_db.py -- creates business.db with coherent fake data.

Run this FIRST. Every other file assumes business.db already exists.
    python seed_db.py
"""

import random
from datetime import date, timedelta

from sqlalchemy import Column, Date, Float, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import Session, declarative_base

Base = declarative_base()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
class Customer(Base):
    __tablename__ = "customers"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    region = Column(String, nullable=False)
    tier = Column(String, nullable=False)  # Bronze / Silver / Gold / Platinum


class Product(Base):
    __tablename__ = "products"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)
    unit_price = Column(Float, nullable=False)


class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=False)
    order_date = Column(Date, nullable=False)
    status = Column(String, nullable=False)  # completed / shipped / processing / cancelled


class OrderItem(Base):
    __tablename__ = "order_items"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    line_total = Column(Float, nullable=False)


# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
FIRST_NAMES = ["Aria", "Noah", "Maya", "Liam", "Zara", "Ethan", "Priya", "Kabir",
               "Lena", "Omar", "Sofia", "Ravi", "Nora", "Theo", "Ines", "Arjun"]
LAST_NAMES = ["Shah", "Nguyen", "Kim", "Silva", "Patel", "Khan", "Rossi", "Berg",
              "Diaz", "Cohen", "Okafor", "Novak", "Suzuki", "Reyes", "Lund", "Iyer"]
REGIONS = ["North", "South", "East", "West", "Central"]
TIERS = ["Bronze", "Silver", "Gold", "Platinum"]
TIER_WEIGHTS = [0.45, 0.30, 0.18, 0.07]  # most customers are entry-tier

PRODUCTS = [
    ("Wireless Earbuds", "Electronics", 79.99),
    ("Standing Desk", "Furniture", 549.99),
    ("Yoga Mat", "Fitness", 24.99),
    ("Espresso Machine", "Appliances", 219.99),
    ("Hiking Backpack", "Outdoor", 89.99),
    ("Mechanical Keyboard", "Electronics", 134.99),
    ("Ceramic Cookware Set", "Kitchen", 159.99),
    ("Running Shoes", "Fitness", 109.99),
]

# repeated "completed" makes most orders complete, like real data
STATUSES = ["completed", "completed", "completed", "shipped", "processing", "cancelled"]


# ---------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------
def seed_database(db_path="business.db", n_customers=100, n_orders=400):
    engine = create_engine(f"sqlite:///{db_path}", echo=False)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)

    with Session(engine) as session:
        customers = [
            Customer(
                id=i,
                name=f"{random.choice(FIRST_NAMES)} {random.choice(LAST_NAMES)}",
                region=random.choice(REGIONS),
                tier=random.choices(TIERS, weights=TIER_WEIGHTS)[0],
            )
            for i in range(1, n_customers + 1)
        ]
        products = [
            Product(id=i, name=n, category=c, unit_price=p)
            for i, (n, c, p) in enumerate(PRODUCTS, start=1)
        ]
        session.add_all(customers + products)
        session.flush()  # assign/validate ids before orders reference them

        orders, items = [], []
        item_id = 1
        start, end = date(2024, 1, 1), date(2025, 6, 30)
        for order_id in range(1, n_orders + 1):
            customer = random.choice(customers)
            order_date = start + timedelta(days=random.randint(0, (end - start).days))
            orders.append(Order(
                id=order_id,
                customer_id=customer.id,
                order_date=order_date,
                status=random.choice(STATUSES),
            ))
            for product in random.sample(products, k=random.randint(1, 4)):
                qty = random.randint(1, 5)
                items.append(OrderItem(
                    id=item_id,
                    order_id=order_id,
                    product_id=product.id,
                    quantity=qty,
                    line_total=round(qty * product.unit_price, 2),
                ))
                item_id += 1

        session.add_all(orders + items)
        session.commit()
        print(
            f"Seeded {db_path}: {len(customers)} customers, {len(products)} products, "
            f"{len(orders)} orders, {len(items)} order items"
        )


if __name__ == "__main__":
    seed_database()
