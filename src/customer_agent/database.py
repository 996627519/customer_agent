"""MySQL storage for accounts, chats, turns, orders, policies, and user preferences."""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    JSON, Boolean, Column, ForeignKey, Index, Integer, MetaData, String, Table, Text,
    create_engine, delete, insert, select, text, update, inspect,
)
from sqlalchemy.engine import URL

from customer_agent.config import settings

metadata = MetaData()
users = Table(
    "ca_users", metadata, Column("id", String(32), primary_key=True),
    Column("username", String(64), nullable=False, unique=True),
    Column("password_hash", String(255), nullable=False), Column("active", Boolean, nullable=False, default=True),
    Column("created_at", String(40), nullable=False),
)
sessions = Table(
    "ca_sessions", metadata, Column("token_hash", String(64), primary_key=True),
    Column("user_id", String(32), ForeignKey("ca_users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("expires_at", Integer, nullable=False, index=True),
)
chats = Table(
    "ca_chats", metadata, Column("id", String(32), primary_key=True),
    Column("user_id", String(32), ForeignKey("ca_users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("title", String(160), nullable=False), Column("summary", Text, nullable=False, default=""),
    Column("summary_cursor", Integer, nullable=False, default=0),
    Column("created_at", String(40), nullable=False), Column("updated_at", String(40), nullable=False),
)
messages = Table(
    "ca_messages", metadata, Column("id", Integer, primary_key=True, autoincrement=True),
    Column("chat_id", String(32), ForeignKey("ca_chats.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("role", String(16), nullable=False), Column("content", Text, nullable=False),
    Column("sources", JSON, nullable=False), Column("created_at", String(40), nullable=False),
)
orders = Table(
    "ca_orders", metadata, Column("id", String(64), primary_key=True),
    Column("user_id", String(32), ForeignKey("ca_users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("product_name", String(200), nullable=False), Column("purchased_at", String(40), nullable=False),
    Column("status", String(64), nullable=False), Column("region", String(100), nullable=True),
    Column("details", JSON, nullable=False),
)
preferences = Table(
    "ca_preferences", metadata, Column("user_id", String(32), ForeignKey("ca_users.id", ondelete="CASCADE"), primary_key=True),
    Column("data", JSON, nullable=False), Column("updated_at", String(40), nullable=False),
)
policy_chunks = Table(
    "ca_policy_chunks", metadata, Column("id", String(32), primary_key=True),
    Column("source", String(255), nullable=False), Column("title", String(255), nullable=False),
    Column("content", Text, nullable=False), Column("region", String(100), nullable=True),
    Column("category", String(100), nullable=True), Column("effective_from", String(20), nullable=True),
    Column("effective_to", String(20), nullable=True), Column("status", String(16), nullable=False, default="draft"),
    Column("updated_at", String(40), nullable=False),
    Index("ix_ca_policy_scope", "region", "category"),
)
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class Database:
    def __init__(self, engine=None):
        if engine is None:
            if not settings.mysql_password:
                raise RuntimeError("请先设置 MYSQL_PASSWORD。")
            url = URL.create(
                "mysql+pymysql", username=settings.mysql_user, password=settings.mysql_password,
                host=settings.mysql_host, port=settings.mysql_port, database=settings.mysql_database,
                query={"charset": "utf8mb4"},
            )
            engine = create_engine(
                url, pool_pre_ping=True, pool_recycle=1800, pool_size=5, max_overflow=10,
                connect_args={"connect_timeout": 5, "read_timeout": 10, "write_timeout": 10},
            )
        self.engine = engine

    def initialize(self) -> None:
        metadata.create_all(self.engine)
        # Existing deployments from the initial scaffold gain a safe, non-published default.
        if "status" not in {column["name"] for column in inspect(self.engine).get_columns("ca_policy_chunks")}:
            with self.engine.begin() as conn:
                conn.execute(text("ALTER TABLE ca_policy_chunks ADD COLUMN status VARCHAR(16) NOT NULL DEFAULT 'draft'"))
        # MySQL full-text search is the initial RAG retriever. Ignore only the duplicate-index case.
        with self.engine.begin() as conn:
            try:
                conn.execute(text("CREATE FULLTEXT INDEX ft_ca_policy_text ON ca_policy_chunks (title, content)"))
            except Exception as exc:
                if "Duplicate key name" not in str(exc) and "1061" not in str(exc):
                    raise

    def check(self) -> None:
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    def close(self) -> None:
        self.engine.dispose()

    def create_user(self, username: str, password_hash: str) -> dict:
        user_id = uuid4().hex
        with self.engine.begin() as conn:
            conn.execute(insert(users).values(id=user_id, username=username, password_hash=password_hash, active=True, created_at=_now()))
        return {"id": user_id, "username": username}

    def find_user(self, username: str):
        with self.engine.connect() as conn:
            row = conn.execute(select(users).where(users.c.username == username)).mappings().first()
            return dict(row) if row else None

    def issue_session(self, user_id: str, ttl_seconds: int) -> str:
        token = secrets.token_urlsafe(32)
        with self.engine.begin() as conn:
            conn.execute(insert(sessions).values(token_hash=_token_hash(token), user_id=user_id, expires_at=int(time.time()) + ttl_seconds))
        return token

    def current_user(self, token: str | None):
        if not token or len(token) > 200:
            return None
        with self.engine.connect() as conn:
            row = conn.execute(select(users.c.id, users.c.username).join(sessions, users.c.id == sessions.c.user_id)
                .where(sessions.c.token_hash == _token_hash(token), sessions.c.expires_at > int(time.time()), users.c.active.is_(True))).mappings().first()
            return dict(row) if row else None

    def delete_session(self, token: str | None) -> None:
        if token:
            with self.engine.begin() as conn:
                conn.execute(delete(sessions).where(sessions.c.token_hash == _token_hash(token)))

    def create_chat(self, user_id: str, title: str) -> dict:
        chat_id, now = uuid4().hex, _now()
        with self.engine.begin() as conn:
            conn.execute(insert(chats).values(id=chat_id, user_id=user_id, title=title[:160], summary="", summary_cursor=0, created_at=now, updated_at=now))
        return {"id": chat_id, "title": title[:160], "summary": ""}

    def get_chat(self, chat_id: str, user_id: str):
        with self.engine.connect() as conn:
            row = conn.execute(select(chats).where(chats.c.id == chat_id, chats.c.user_id == user_id)).mappings().first()
            return dict(row) if row else None

    def list_chats(self, user_id: str):
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(chats.c.id, chats.c.title, chats.c.created_at, chats.c.updated_at)
                .where(chats.c.user_id == user_id).order_by(chats.c.updated_at.desc()).limit(100)).mappings()]

    def delete_chat(self, chat_id: str, user_id: str) -> bool:
        with self.engine.begin() as conn:
            result = conn.execute(delete(chats).where(chats.c.id == chat_id, chats.c.user_id == user_id))
            return result.rowcount > 0

    def get_messages(self, chat_id: str, limit: int):
        with self.engine.connect() as conn:
            rows = conn.execute(select(messages.c.role, messages.c.content, messages.c.sources)
                .where(messages.c.chat_id == chat_id).order_by(messages.c.id.desc()).limit(limit)).mappings().all()
            return [dict(r) for r in reversed(rows)]

    def get_all_messages(self, chat_id: str):
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(messages.c.id, messages.c.role, messages.c.content)
                .where(messages.c.chat_id == chat_id).order_by(messages.c.id)).mappings()]

    def append_turn(self, chat_id: str, user_content: str, assistant_content: str, sources: list[dict]) -> None:
        now = _now()
        with self.engine.begin() as conn:
            conn.execute(insert(messages), [
                {"chat_id": chat_id, "role": "user", "content": user_content, "sources": [], "created_at": now},
                {"chat_id": chat_id, "role": "assistant", "content": assistant_content, "sources": sources, "created_at": now},
            ])
            conn.execute(update(chats).where(chats.c.id == chat_id).values(updated_at=now))

    def save_summary(self, chat_id: str, summary: str, cursor: int) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(chats).where(chats.c.id == chat_id).values(summary=summary, summary_cursor=cursor, updated_at=_now()))

    def get_order_for_user(self, user_id: str, order_id: str):
        with self.engine.connect() as conn:
            row = conn.execute(select(orders).where(orders.c.id == order_id, orders.c.user_id == user_id)).mappings().first()
            if not row:
                return None
            result = dict(row)
            metadata_values = result.pop("details", {}) or {}
            result.update(metadata_values)
            return result

    def put_order(self, *, user_id: str, order_id: str, product_name: str, purchased_at: str,
                  status: str, region: str | None = None, category: str | None = None):
        values = {"id": order_id, "user_id": user_id, "product_name": product_name, "purchased_at": purchased_at,
                  "status": status, "region": region, "details": {"category": category} if category else {}}
        with self.engine.begin() as conn:
            found = conn.execute(select(orders.c.id).where(orders.c.id == order_id, orders.c.user_id == user_id)).first()
            if found:
                conn.execute(update(orders).where(orders.c.id == order_id, orders.c.user_id == user_id).values(**values))
            else:
                conn.execute(insert(orders).values(**values))

    def search_policies(self, query: str, *, region: str | None = None, category: str | None = None, limit: int = 5):
        terms = [w for w in re.findall(r"[A-Za-z0-9_-]{2,}|[\u4e00-\u9fff]{2,}", query)]
        category_values = [category] if category else []
        # A running shoe is a specific kind of shoe. Allow its query to match
        # a general footwear policy, but never broaden a general shoe query to
        # a policy scoped only to running shoes.
        if category == "跑鞋":
            category_values.append("鞋类")
        category_names = [f"category{i}" for i in range(len(category_values))]
        category_params = dict(zip(category_names, category_values))
        category_clause = (
            f"(category IS NULL OR category IN ({', '.join(':' + name for name in category_names)}))"
            if category_names else ""
        )
        with self.engine.connect() as conn:
            if terms:
                sql = "SELECT id, source, title, content, region, category, effective_from, effective_to, MATCH(title,content) AGAINST (:q IN NATURAL LANGUAGE MODE) AS score FROM ca_policy_chunks WHERE status = 'published' AND MATCH(title,content) AGAINST (:q IN NATURAL LANGUAGE MODE)"
                sql += " AND (effective_from IS NULL OR effective_from <= :today) AND (effective_to IS NULL OR effective_to >= :today)"
                params = {"q": " ".join(terms), "limit": limit, "today": date.today().isoformat()}
                if region:
                    sql += " AND (region IS NULL OR region = :region)"; params["region"] = region
                if category_clause:
                    sql += " AND " + category_clause; params.update(category_params)
                sql += " ORDER BY score DESC LIMIT :limit"
                rows = conn.execute(text(sql), params).mappings().all()
                if rows:
                    return [dict(r) for r in rows]
            # Fallback also handles short Chinese search terms that MySQL FULLTEXT tokenization misses.
            where = ["status = 'published'"]
            params = {"limit": limit, "today": date.today().isoformat()}
            search_terms = []
            for term in terms:
                if re.fullmatch(r"[\u4e00-\u9fff]+", term):
                    search_terms.extend(term[i:i + 2] for i in range(len(term) - 1))
                else:
                    search_terms.append(term)
            search_terms = list(dict.fromkeys(search_terms))[:12]
            matches = []
            for i, term in enumerate(search_terms):
                key = f"term{i}"; matches.append(f"(title LIKE :{key} OR content LIKE :{key})"); params[key] = f"%{term}%"
            if matches:
                where.append("(" + " OR ".join(matches) + ")")
            if region:
                where.append("(region IS NULL OR region=:region)"); params["region"] = region
            if category_clause:
                where.append(category_clause); params.update(category_params)
            sql = "SELECT id, source, title, content, region, category, effective_from, effective_to FROM ca_policy_chunks"
            where.extend(["(effective_from IS NULL OR effective_from <= :today)", "(effective_to IS NULL OR effective_to >= :today)"])
            if where: sql += " WHERE " + " AND ".join(where)
            sql += " ORDER BY updated_at DESC LIMIT :limit"
            return [dict(r) for r in conn.execute(text(sql), params).mappings().all()]

    def get_preferences(self, user_id: str):
        with self.engine.connect() as conn:
            return conn.execute(select(preferences.c.data).where(preferences.c.user_id == user_id)).scalar_one_or_none() or {}

    def save_preferences(self, user_id: str, data: dict) -> None:
        with self.engine.begin() as conn:
            exists = conn.execute(select(preferences.c.user_id).where(preferences.c.user_id == user_id)).first()
            if exists:
                conn.execute(update(preferences).where(preferences.c.user_id == user_id).values(data=data, updated_at=_now()))
            else:
                conn.execute(insert(preferences).values(user_id=user_id, data=data, updated_at=_now()))

    def add_policy(self, *, source: str, title: str, content: str, region=None, category=None,
                   effective_from=None, effective_to=None, status="draft"):
        with self.engine.begin() as conn:
            conn.execute(insert(policy_chunks).values(id=uuid4().hex, source=source, title=title, content=content,
                region=region, category=category, effective_from=effective_from, effective_to=effective_to,
                status=status, updated_at=_now()))
