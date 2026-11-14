"""Routes available to anonymous viewers and login/logout endpoints."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .. import audit, auth
from ..config import ROOT
from . import handlers
from .limits import check_ask_rate, enter_ask_slot, leave_ask_slot

router = APIRouter()


class PublicAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


def _file_response(root: Path, relative_path: str, not_found: str):
    base = root.resolve()
    path = (base / relative_path).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(404, not_found)
    return FileResponse(path)


def _safe_source_link(source: dict) -> str | None:
    if source.get("source_type") == "document" and source.get("document_id") and source.get("section"):
        return f"/api/documents/{quote(str(source['document_id']), safe='')}/sections/{int(source['section'])}"
    link = source.get("link") or ""
    video_id = str(source.get("video_id") or "")
    start = max(0, int(source.get("start") or 0))
    if link.startswith("/api/videos/") and video_id:
        return f"/api/videos/{quote(video_id, safe='')}/file#t={start}"
    try:
        parsed = urlsplit(link)
        host = (parsed.hostname or "").lower()
    except ValueError:
        return None
    if video_id and (host == "youtu.be" or host.endswith(("youtube.com", "youtube-nocookie.com"))):
        return f"https://www.youtube.com/watch?v={quote(video_id, safe='')}&t={start}s"
    return None


@router.get("/")
def user_home():
    return FileResponse(ROOT / "web" / "user" / "index.html")


@router.get("/user/assets/{asset_path:path}")
def user_asset(asset_path: str):
    return _file_response(ROOT / "web" / "user", asset_path, "Không tìm thấy tài nguyên.")


@router.get("/shared/{asset_path:path}")
def shared_asset(asset_path: str):
    return _file_response(ROOT / "web" / "shared", asset_path, "Không tìm thấy tài nguyên.")


@router.get("/admin/login")
def admin_login_page():
    return FileResponse(ROOT / "web" / "admin" / "login.html", headers={"X-Robots-Tag": "noindex, nofollow"})


@router.get("/admin/login/assets/{asset_path:path}")
def admin_login_asset(asset_path: str):
    return _file_response(ROOT / "web" / "admin" / "login-assets", asset_path, "Không tìm thấy tài nguyên.")


@router.get("/api/videos")
def public_videos():
    return handlers.public_videos()


@router.get("/api/catalog")
def public_catalog():
    return handlers.public_catalog()


@router.get("/api/documents/{document_id}/sections/{section}")
def public_document_section(document_id: str, section: int):
    if not handlers._indexed_document(document_id):
        raise HTTPException(404, "Không tìm thấy mục tài liệu.")
    return handlers.document_section(document_id, section)


@router.get("/api/videos/{video_id}/file")
def public_video_file(video_id: str):
    if not handlers._indexed_video(video_id):
        raise HTTPException(404, "Không tìm thấy video.")
    return handlers.uploaded_video_file(video_id)


@router.get("/api/status")
def public_status():
    return {"ready": handlers.state["service"] is not None}


@router.post("/api/ask")
def ask(request: Request, payload: PublicAskRequest):
    check_ask_rate(auth.client_ip(request))
    enter_ask_slot()
    try:
        result = handlers.ask(handlers.AskRequest(question=payload.question), use_request_mode=False)
        sources = []
        for source in result.get("sources", []):
            item = {
                key: source[key]
                for key in ("n", "source_type", "video_id", "document_id", "section", "title", "label", "start", "end", "text", "cited")
                if key in source
            }
            item["link"] = _safe_source_link(source)
            sources.append(item)
        return {"answer": result["answer"], "found": result["found"], "cited": result["cited"], "sources": sources}
    except HTTPException as exc:
        if exc.status_code == 503:
            raise
        logging.info("Public ask returned status %s", exc.status_code)
        raise
    finally:
        leave_ask_slot()


@router.get("/api/auth/me")
def auth_me(request: Request):
    token = request.cookies.get(auth.COOKIE_NAME)
    session = auth.get_session(token)
    if session is None or not auth.admin_is_configured():
        return {"admin": False}
    return {"admin": True, "csrf_token": session["csrf"], "admin_url": "/admin"}


@router.post("/api/auth/login")
def auth_login(request: Request, payload: LoginRequest):
    if not auth._same_origin(request):
        audit.write_event("auth.login.failure", result="origin_rejected")
        raise HTTPException(403, "Yêu cầu đăng nhập không hợp lệ.")
    try:
        created = auth.login(request, payload.username, payload.password)
    except HTTPException as exc:
        audit.write_event("auth.login.failure", result="throttled" if exc.status_code == 429 else "disabled_or_rejected")
        raise
    if created is None:
        audit.write_event("auth.login.failure", result="invalid_credentials")
        raise HTTPException(401, "Sai tên đăng nhập hoặc mật khẩu")
    token, session = created
    response = JSONResponse({"admin": True, "csrf_token": session["csrf"]})
    auth.set_session_cookie(response, token)
    audit.write_event("auth.login.success")
    return response


@router.post("/api/auth/logout")
async def auth_logout(request: Request):
    auth.validate_admin_request(request)
    auth.revoke_session(request.cookies.get(auth.COOKIE_NAME))
    response = JSONResponse({"logged_out": True})
    auth.clear_session_cookie(response)
    audit.write_event("auth.logout", result="success")
    return response
