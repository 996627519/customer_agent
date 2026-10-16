"""FastAPI web application: cookie authentication, per-user chats, and LangGraph invocation."""

from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.exc import OperationalError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from customer_agent.config import settings
from customer_agent.database import Database
from customer_agent.graph import build_graph
from customer_agent.services.llm import get_deepseek
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

logger = logging.getLogger(__name__)
PASSWORDS = PasswordHasher()
DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(24))
SESSION_COOKIE = "ca_session"
CSRF_COOKIE = "ca_csrf"
_SOURCE_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
FRONTEND = _SOURCE_FRONTEND if _SOURCE_FRONTEND.is_dir() else Path.cwd() / "frontend"


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32, pattern=r"^[a-zA-Z0-9_][a-zA-Z0-9_.-]{2,31}$")
    password: str = Field(min_length=1, max_length=128)


class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=settings.max_message_chars)
    chat_id: str | None = Field(default=None, min_length=32, max_length=32)


class PreferencesInput(BaseModel):
    language: str | None = Field(default=None, max_length=32)
    communication_style: str | None = Field(default=None, max_length=80)


class LoginLimiter:
    def __init__(self):
        self.entries = defaultdict(deque)

    def check(self, key: str, maximum: int = 8):
        now = time.monotonic()
        queue = self.entries[key]
        while queue and queue[0] < now - 300:
            queue.popleft()
        if len(queue) >= maximum:
            raise HTTPException(429, "尝试次数较多，请稍后再试。")
        queue.append(now)


def create_app(*, database: Database | None = None):
    store = database or Database()
    limiter = LoginLimiter()

    @asynccontextmanager
    async def lifespan(app):
        app.state.graph = build_graph(store)
        app.state.database_ready = False
        try:
            await asyncio.to_thread(store.initialize)
            await asyncio.to_thread(store.check)
            app.state.database_ready = True
        except OperationalError:
            # Keep the UI available so a local developer sees a useful DB error instead of a blank page.
            logger.exception("MySQL initialization failed; the UI will remain available in read-only error mode.")
        yield
        if database is None:
            await asyncio.to_thread(store.close)

    app = FastAPI(title=settings.app_name, lifespan=lifespan, docs_url=None, redoc_url=None)
    hosts = [h.strip() for h in settings.allowed_hosts.split(",") if h.strip()]
    if not hosts or "*" in hosts:
        raise ValueError("ALLOWED_HOSTS 必须设置为明确的主机名或 IP。")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)
    app.state.store = store

    @app.middleware("http")
    async def security(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api") and request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "请求来源不匹配。"}, status_code=403)
            cookie_csrf = request.cookies.get(CSRF_COOKIE, "")
            header_csrf = request.headers.get("x-csrf-token", "")
            if not cookie_csrf or not hmac.compare_digest(cookie_csrf, header_csrf):
                return JSONResponse({"detail": "页面会话已失效，请刷新后重试。"}, status_code=403)
        if path.startswith("/api/") and path not in {"/api/auth/csrf", "/api/auth/register", "/api/auth/login"}:
            try:
                user = await asyncio.to_thread(store.current_user, request.cookies.get(SESSION_COOKIE))
            except OperationalError:
                return JSONResponse({"detail": "账号服务暂时无法连接 MySQL，请检查数据库服务和账号配置。"}, status_code=503)
            if not user:
                return JSONResponse({"detail": "请先登录。"}, status_code=401)
            request.state.user = user
        response = await call_next(request)
        response.headers.update({
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
            "X-Frame-Options": "DENY", "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'",
        })
        if path.startswith("/api"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def csrf_response(payload, request: Request, *, rotate=False):
        token = None if rotate else request.cookies.get(CSRF_COOKIE)
        token = token or secrets.token_urlsafe(32)
        result = JSONResponse({**payload, "csrf_token": token})
        result.set_cookie(CSRF_COOKIE, token, httponly=True, samesite="strict", secure=settings.cookie_secure, path="/")
        return result

    @app.get("/healthz")
    async def health():
        try:
            await asyncio.to_thread(store.check)
        except OperationalError:
            raise HTTPException(503, "MySQL unavailable") from None
        return {"status": "ok"}

    @app.get("/api/auth/csrf")
    async def csrf(request: Request):
        return csrf_response({"registration_enabled": settings.allow_registration}, request)

    @app.post("/api/auth/register", status_code=201)
    async def register(data: Credentials, request: Request):
        if not settings.allow_registration:
            raise HTTPException(403, "当前不开放注册，请联系管理员创建账号。")
        limiter.check(f"register:{request.client.host if request.client else 'local'}", maximum=5)
        if len(data.password) < 10:
            raise HTTPException(422, "新密码至少需要 10 个字符。")
        try:
            return await asyncio.to_thread(store.create_user, data.username.casefold(), PASSWORDS.hash(data.password))
        except OperationalError:
            raise HTTPException(503, "账号服务暂时无法连接数据库，请检查 MySQL 服务和账号配置。") from None
        except Exception as exc:
            if "Duplicate entry" in str(exc) or "1062" in str(exc):
                raise HTTPException(409, "该用户名已被使用。") from None
            raise

    @app.post("/api/auth/login")
    async def login(data: Credentials, request: Request):
        ip = request.client.host if request.client else "local"
        limiter.check(f"login-ip:{ip}", maximum=40)
        limiter.check(f"login-user:{ip}:{data.username.casefold()}")
        try:
            user = await asyncio.to_thread(store.find_user, data.username.casefold())
        except OperationalError:
            raise HTTPException(503, "账号服务暂时无法连接数据库，请检查 MySQL 服务和账号配置。") from None
        try:
            valid = PASSWORDS.verify(user["password_hash"] if user else DUMMY_HASH, data.password)
        except (VerificationError, InvalidHashError):
            valid = False
        if not valid or not user or not user["active"]:
            raise HTTPException(401, "用户名或密码不正确。")
        await asyncio.to_thread(store.delete_session, request.cookies.get(SESSION_COOKIE))
        token = await asyncio.to_thread(store.issue_session, user["id"], settings.session_hours * 3600)
        result = csrf_response({"user": {"id": user["id"], "username": user["username"]}}, request, rotate=True)
        result.set_cookie(SESSION_COOKIE, token, max_age=settings.session_hours * 3600, httponly=True,
                          samesite="strict", secure=settings.cookie_secure, path="/")
        return result

    @app.post("/api/auth/logout")
    async def logout(request: Request):
        await asyncio.to_thread(store.delete_session, request.cookies.get(SESSION_COOKIE))
        result = csrf_response({"message": "已退出"}, request, rotate=True)
        result.delete_cookie(SESSION_COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="strict")
        return result

    @app.get("/api/session")
    async def session(request: Request):
        return {"user": request.state.user, "model_ready": bool(settings.deepseek_api_key)}

    @app.get("/api/chats")
    async def list_chats(request: Request):
        return await asyncio.to_thread(store.list_chats, request.state.user["id"])

    @app.post("/api/chats", status_code=201)
    async def create_chat(request: Request):
        return await asyncio.to_thread(store.create_chat, request.state.user["id"], "新的客服对话")

    @app.get("/api/chats/{chat_id}")
    async def get_chat(chat_id: str, request: Request):
        chat = await asyncio.to_thread(store.get_chat, chat_id, request.state.user["id"])
        if not chat:
            raise HTTPException(404, "对话不存在。")
        chat["messages"] = await asyncio.to_thread(store.get_messages, chat_id, 200)
        return chat

    @app.delete("/api/chats/{chat_id}")
    async def delete_chat(chat_id: str, request: Request):
        deleted = await asyncio.to_thread(store.delete_chat, chat_id, request.state.user["id"])
        if not deleted:
            raise HTTPException(404, "对话不存在。")
        return {"deleted": True}

    @app.post("/api/chat")
    async def chat(data: ChatInput, request: Request):
        user_id = request.state.user["id"]
        message = data.message.strip()
        if not message:
            raise HTTPException(422, "请输入消息。")
        chat_obj = await asyncio.to_thread(store.get_chat, data.chat_id, user_id) if data.chat_id else None
        if data.chat_id and not chat_obj:
            raise HTTPException(404, "对话不存在。")
        if not chat_obj:
            title = message[:48] + ("…" if len(message) > 48 else "")
            chat_obj = await asyncio.to_thread(store.create_chat, user_id, title)
        history = await asyncio.to_thread(store.get_messages, chat_obj["id"], settings.max_history_messages)
        messages = []
        if chat_obj.get("summary"):
            messages.append(SystemMessage(content="较早对话摘要（仅作上下文，不代表已核实事实）：" + chat_obj["summary"][:4000]))
        budget = max(2000, settings.max_context_chars - sum(len(str(m.content)) for m in messages) - 1000)
        selected = []
        for item in reversed(history):
            content = item["content"]
            if budget <= 0:
                break
            if len(content) > budget:
                content = content[-budget:]
            selected.append((item["role"], content))
            budget -= len(content)
        selected.reverse()
        messages.extend(HumanMessage(content=content) if role == "user" else AIMessage(content=content) for role, content in selected)
        messages.append(HumanMessage(content=message))
        try:
            result = await request.app.state.graph.ainvoke({"messages": messages, "user_id": user_id})
        except Exception as exc:
            logger.exception("Agent invocation failed")
            raise HTTPException(502, "客服服务暂时不可用，请稍后重试。") from exc
        answer = result.get("answer", "暂时无法生成答复，请稍后重试。")
        sources = result.get("answer_sources", [])
        await asyncio.to_thread(store.append_turn, chat_obj["id"], message, answer, sources)
        # Rolling summary: once the persisted conversation exceeds the active window, summarize older turns.
        all_messages = await asyncio.to_thread(store.get_all_messages, chat_obj["id"])
        if len(all_messages) > settings.max_history_messages * 2:
            cutoff_index = len(all_messages) - settings.max_history_messages
            old = all_messages[:cutoff_index]
            if old and old[-1]["id"] > chat_obj.get("summary_cursor", 0):
                try:
                    prior = chat_obj.get("summary", "")
                    summary_prompt = [
                        SystemMessage(content="把客服对话压缩成简短、无猜测的摘要。保留未解决问题、用户明确提供的商品/订单关联和已说明的政策结论；明确区分用户陈述与已核实事实。不要保存支付凭证、地址等敏感内容。"),
                        HumanMessage(content=f"已有摘要：{prior}\n新增对话：" + "\n".join(f"{m['role']}: {m['content'][:1000]}" for m in old if m["id"] > chat_obj.get("summary_cursor", 0))),
                    ]
                    summary_result = await get_deepseek(temperature=0).ainvoke(summary_prompt)
                    await asyncio.to_thread(store.save_summary, chat_obj["id"], str(summary_result.content)[:6000], old[-1]["id"])
                except Exception:
                    logger.warning("Conversation summarization failed; recent history remains available.", exc_info=True)
        return {"chat_id": chat_obj["id"], "answer": answer, "sources": sources}

    @app.get("/api/preferences")
    async def get_preferences(request: Request):
        return await asyncio.to_thread(store.get_preferences, request.state.user["id"])

    @app.put("/api/preferences")
    async def put_preferences(data: PreferencesInput, request: Request):
        values = {k: v for k, v in data.model_dump().items() if v}
        await asyncio.to_thread(store.save_preferences, request.state.user["id"], values)
        return values

    @app.delete("/api/preferences")
    async def delete_preferences(request: Request):
        await asyncio.to_thread(store.save_preferences, request.state.user["id"], {})
        return {"deleted": True}

    app.mount("/assets", StaticFiles(directory=FRONTEND / "assets"), name="assets")

    @app.get("/{path:path}")
    async def frontend(path: str):
        if path.startswith("api/") or path == "healthz":
            raise HTTPException(404, "Not found")
        return FileResponse(FRONTEND / "index.html")

    return app
