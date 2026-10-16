"""Administrative commands for initializing MySQL and loading data."""

import argparse
import getpass
from pathlib import Path

from argon2 import PasswordHasher
from sqlalchemy import select

from customer_agent.config import settings
from customer_agent.database import Database, orders


def main():
    parser = argparse.ArgumentParser(description="Customer Agent database administration")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="Create application tables and full-text index")
    seed_demo = sub.add_parser("seed-demo", help="Create demo accounts, orders, and memory data")
    seed_demo.add_argument(
        "--allow-remote", action="store_true",
        help="Explicitly allow fixed demo records to be seeded into a non-local MySQL host",
    )
    create_user = sub.add_parser("create-user", help="Create a login account")
    create_user.add_argument("username")
    order = sub.add_parser("upsert-order", help="Import/update one order owned by an account")
    order.add_argument("--username", required=True)
    order.add_argument("--order-id", required=True)
    order.add_argument("--product", required=True)
    order.add_argument("--purchased-at", required=True, help="ISO date, e.g. 2026-10-01")
    order.add_argument("--status", required=True, help="Order/product state from the source system")
    order.add_argument("--region")
    order.add_argument("--category")
    add_policy = sub.add_parser("add-policy", help="Add one policy passage to the RAG index")
    add_policy.add_argument("--title", required=True)
    add_policy.add_argument("--source", required=True)
    add_policy.add_argument("--file", required=True, type=Path)
    add_policy.add_argument("--region")
    add_policy.add_argument("--category")
    add_policy.add_argument("--effective-from")
    add_policy.add_argument("--effective-to")
    add_policy.add_argument("--status", choices=("draft", "published"), default="draft")
    args = parser.parse_args()
    db = Database()
    try:
        if args.command == "seed-demo":
            if settings.app_env.casefold() not in {"development", "dev", "local"}:
                raise SystemExit("seed-demo 仅允许在 APP_ENV=development/dev/local 时运行。")
            if settings.mysql_host.casefold() not in {"localhost", "127.0.0.1", "::1"} and not args.allow_remote:
                raise SystemExit("seed-demo 仅允许连接本机 MySQL。")
        db.initialize()
        if args.command == "init-db":
            print("MySQL tables and policy search index are ready.")
        elif args.command == "seed-demo":
            demo_password = "DemoPass123!"
            passwords = PasswordHasher()
            demo_users = {}
            created = []
            for username in ("demo_customer", "demo_other"):
                user = db.find_user(username)
                if user is None:
                    user = db.create_user(username, passwords.hash(demo_password))
                    created.append(username)
                demo_users[username] = user

            demo_orders = [
                ("DEMO-A123", "demo_customer", "跑鞋", "2026-10-10", "已签收", "跑鞋"),
                ("DEMO-A124", "demo_customer", "休闲鞋", "2026-10-12", "运输中", "鞋类"),
                ("DEMO-A125", "demo_customer", "棉质卫衣", "2026-10-11", "已签收", "服饰"),
                ("DEMO-A126", "demo_customer", "无线耳机", "2026-10-09", "已签收", "电子产品"),
                ("DEMO-A127", "demo_customer", "LED台灯", "2026-10-15", "待发货", "家居用品"),
                ("DEMO-A128", "demo_customer", "科幻小说", "2026-10-07", "已签收", "图书"),
                ("DEMO-A129", "demo_customer", "保湿面霜", "2026-10-06", "已签收", "美妆"),
                ("DEMO-A130", "demo_customer", "咖啡豆", "2026-10-13", "已签收", "食品"),
                ("DEMO-A131", "demo_customer", "猫粮", "2026-10-14", "已签收", "宠物用品"),
                ("DEMO-A132", "demo_customer", "餐桌", "2026-10-08", "运输中", "家具"),
                ("DEMO-A133", "demo_customer", "运动水壶", "2026-10-15", "已取消", "户外运动"),
                ("DEMO-B456", "demo_other", "篮球鞋", "2026-10-08", "已签收", "鞋类"),
                ("DEMO-B457", "demo_other", "冷藏水果礼盒", "2026-10-15", "已签收", "生鲜"),
                ("DEMO-B458", "demo_other", "轻薄笔记本电脑", "2026-10-05", "已签收", "电子产品"),
                ("DEMO-B459", "demo_other", "婴儿推车", "2026-10-14", "待发货", "母婴"),
                ("DEMO-B460", "demo_other", "双人帐篷", "2026-10-03", "已退货", "户外运动"),
            ]
            with db.engine.connect() as conn:
                for order_id, username, *_ in demo_orders:
                    existing = conn.execute(select(orders.c.user_id).where(orders.c.id == order_id)).first()
                    if existing and existing.user_id != demo_users[username]["id"]:
                        raise SystemExit(f"测试订单号 {order_id} 已属于其他账号；未覆盖该订单。")

            for order_id, username, product, purchased_at, status, category in demo_orders:
                db.put_order(
                    user_id=demo_users[username]["id"], order_id=order_id, product_name=product,
                    purchased_at=purchased_at, status=status, region="中国大陆", category=category,
                )

            customer_id = demo_users["demo_customer"]["id"]
            if not db.get_preferences(customer_id):
                db.save_preferences(customer_id, {"language": "中文", "communication_style": "简洁"})
            other_id = demo_users["demo_other"]["id"]
            if not db.get_preferences(other_id):
                db.save_preferences(other_id, {"language": "中文", "communication_style": "详细"})

            print(f"Demo data ready: {len(demo_orders)} orders across 13 category labels and multiple order states.")
            print("Demo-only password for newly created accounts: DemoPass123!")
            if created:
                print("Created accounts: " + ", ".join(created))
            else:
                print("Both demo accounts already existed; their passwords were not changed.")
        elif args.command == "create-user":
            password = getpass.getpass("Password (at least 10 characters): ")
            if len(password) < 10:
                raise SystemExit("Password must contain at least 10 characters.")
            user = db.create_user(args.username.strip().casefold(), PasswordHasher().hash(password))
            print(f"Created account: {user['username']} ({user['id']})")
        elif args.command == "upsert-order":
            user = db.find_user(args.username.strip().casefold())
            if not user:
                raise SystemExit("Account not found.")
            db.put_order(user_id=user["id"], order_id=args.order_id, product_name=args.product,
                         purchased_at=args.purchased_at, status=args.status, region=args.region, category=args.category)
            print(f"Imported order {args.order_id} for {user['username']}.")
        elif args.command == "add-policy":
            content = args.file.read_text(encoding="utf-8").strip()
            if not content:
                raise SystemExit("Policy file is empty.")
            chunks, current = [], ""
            for paragraph in content.splitlines():
                paragraph = paragraph.strip()
                if not paragraph:
                    continue
                if current and len(current) + len(paragraph) + 1 > 1400:
                    chunks.append(current); current = ""
                current = f"{current}\n{paragraph}".strip()
            if current:
                chunks.append(current)
            for index, chunk in enumerate(chunks, start=1):
                db.add_policy(source=args.source, title=f"{args.title}（片段 {index}/{len(chunks)}）", content=chunk,
                              region=args.region, category=args.category,
                              effective_from=args.effective_from, effective_to=args.effective_to, status=args.status)
            print(f"Added {len(chunks)} policy passage(s): {args.title}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
