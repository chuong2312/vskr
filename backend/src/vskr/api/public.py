"""Learner app, learner authentication, and the deliberately small admin login surface."""

from __future__ import annotations

import hmac
import logging
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .. import accounts, audit, auth
from ..config import ROOT
from . import handlers
from .limits import check_ask_rate, enter_ask_slot, leave_ask_slot

router = APIRouter()


class PublicAskRequest(BaseModel):
    question: str


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


class RegisterRequest(LoginRequest):
    invite_code: str = Field(min_length=1, max_length=256)


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
    youtube_id = str(source.get("youtube_id") or video_id)
    if video_id and (
        host == "youtu.be"
        or host == "youtube.com"
        or host.endswith(".youtube.com")
        or host == "youtube-nocookie.com"
        or host.endswith(".youtube-nocookie.com")
    ):
        if host == "youtu.be" and not source.get("youtube_id"):
            youtube_id = parsed.path.strip("/").split("/")[0]
        elif not source.get("youtube_id"):
            youtube_id = video_id
        return f"https://www.youtube.com/watch?v={quote(youtube_id, safe='')}&t={start}s"
    return handlers._safe_source_url(link)


@router.get("/")
def user_home(request: Request):
    if not accounts.get_session(request.cookies.get(accounts.COOKIE_NAME)):
        return RedirectResponse("/login", status_code=303)
    return FileResponse(ROOT / "web" / "user" / "index.html")


@router.get("/login")
def user_login_page():
    return FileResponse(ROOT / "web" / "user" / "login.html", headers={"X-Robots-Tag": "noindex, nofollow"})


@router.get("/api/user/auth/config")
def user_auth_config():
    return {"registration_enabled": bool(os.environ.get("VSKR_USER_INVITE_CODE", "").strip())}


@router.post("/api/user/auth/register")
def user_register(payload: RegisterRequest, request: Request):
    if not auth._secure_cookie_request(request) or not auth._same_origin(request):
        raise HTTPException(403, "Yêu cầu đăng ký không hợp lệ.")
    retry = auth.login_retry_after(auth.client_ip(request), payload.username)
    if retry:
        raise HTTPException(429, "Thử lại đăng ký sau.", headers={"Retry-After": str(retry)})
    invite = os.environ.get("VSKR_USER_INVITE_CODE", "").strip()
    if invite and not hmac.compare_digest(payload.invite_code.encode(), invite.encode()):
        auth._record_login_failure(auth.client_ip(request), payload.username)
        raise HTTPException(403, "Mã mời không hợp lệ.")
    user = accounts.register(payload.username, payload.password, payload.invite_code)
    auth._clear_login_failures(auth.client_ip(request), payload.username)
    token, session = accounts.create_session(payload.username, payload.password)
    response = JSONResponse({"user": {"user_id": user["user_id"], "username": user["username"]}, "csrf_token": session["csrf_token"]})
    accounts.set_cookie(response, token)
    return response


@router.post("/api/user/auth/login")
def user_login(payload: LoginRequest, request: Request):
    if not auth._secure_cookie_request(request) or not auth._same_origin(request):
        raise HTTPException(403, "Yêu cầu đăng nhập không hợp lệ.")
    ip = auth.client_ip(request)
    retry = auth.login_retry_after(ip, payload.username)
    if retry:
        raise HTTPException(429, "Sai tên đăng nhập hoặc mật khẩu.", headers={"Retry-After": str(retry)})
    try:
        token, session = accounts.create_session(payload.username, payload.password)
    except HTTPException as exc:
        if exc.status_code == 401:
            auth._record_login_failure(ip, payload.username)
        raise
    auth._clear_login_failures(ip, payload.username)
    response = JSONResponse({"user": {"user_id": session["user_id"], "username": session["username"]}, "csrf_token": session["csrf_token"]})
    accounts.set_cookie(response, token)
    return response


@router.post("/api/user/auth/logout")
def user_logout(request: Request, user=Depends(accounts.require_user)):
    accounts.revoke_session(request.cookies.get(accounts.COOKIE_NAME))
    response = JSONResponse({"ok": True})
    accounts.clear_cookie(response)
    return response


@router.get("/api/user/auth/me")
def user_me(user=Depends(accounts.require_user)):
    return {"user_id": user["user_id"], "username": user["username"], "csrf_token": user["csrf_token"]}


@router.get("/user/assets/{asset_path:path}")
def user_asset(asset_path: str):
    return _file_response(ROOT / "web" / "user", asset_path, "Không tìm thấy tài nguyên.")


@router.get("/shared/{asset_path:path}")
def shared_asset(asset_path: str):
    return _file_response(ROOT / "web" / "shared", asset_path, "Không tìm thấy tài nguyên.")


@router.get("/manifest.webmanifest")
def user_manifest():
    path = ROOT / "web" / "user" / "manifest.webmanifest"
    if not path.is_file():
        raise HTTPException(404, "Không tìm thấy tài nguyên.")
    return FileResponse(path, media_type="application/manifest+json")


@router.get("/sw.js")
def user_service_worker():
    path = ROOT / "web" / "user" / "sw.js"
    if not path.is_file():
        raise HTTPException(404, "Không tìm thấy tài nguyên.")
    return FileResponse(path, media_type="application/javascript", headers={
        "Service-Worker-Allowed": "/",
        "Cache-Control": "no-cache",
    })


@router.get("/admin/login")
def admin_login_page():
    return FileResponse(ROOT / "web" / "admin" / "login.html", headers={"X-Robots-Tag": "noindex, nofollow"})


@router.get("/admin/login/assets/{asset_path:path}")
def admin_login_asset(asset_path: str):
    return _file_response(ROOT / "web" / "admin" / "login-assets", asset_path, "Không tìm thấy tài nguyên.")


@router.get("/api/videos")
def public_videos(user=Depends(accounts.require_learner_or_admin)):
    return handlers.public_videos(owner_id=user["user_id"])


@router.get("/api/catalog")
def public_catalog(user=Depends(accounts.require_learner_or_admin)):
    return handlers.public_catalog(owner_id=user["user_id"])


@router.post("/api/ingest", status_code=202)
async def public_ingest(url: str | None = Form(None), file: UploadFile | None = File(None), user=Depends(accounts.require_learner_or_admin)):
    result = await handlers.start_ingest(url=url, file=file, owner_id=user.get("user_id"))
    audit.write_event("ingest.start", target_id=result.get("job_id"), kind="upload" if file else "url")
    return result


@router.post("/api/documents", status_code=202)
async def public_upload_document(file: UploadFile = File(...), user=Depends(accounts.require_learner_or_admin)):
    result = await handlers.upload_document(file=file, owner_id=user.get("user_id"))
    audit.write_event("document.upload", target_id=result.get("job_id"), kind="document")
    return result


@router.get("/api/jobs/{job_id}")
def public_job_status(job_id: str, user=Depends(accounts.require_learner_or_admin)):
    job = handlers.get_job(job_id)
    if not user.get("admin") and job.get("owner_id") != user["user_id"]:
        raise HTTPException(404, "Không tìm thấy tác vụ.")
    if user.get("admin"):
        return job
    return {key: job[key] for key in ("job_id", "status", "phase", "message", "title") if key in job}


@router.get("/api/documents/{document_id}/sections/{section}")
def public_document_section(document_id: str, section: int, user=Depends(accounts.require_learner_or_admin)):
    if not handlers._indexed_document(document_id):
        raise HTTPException(404, "Không tìm thấy mục tài liệu.")
    return handlers.document_section(document_id, section, owner_id=user.get("user_id"))


@router.get("/api/videos/{video_id}/file")
def public_video_file(video_id: str, user=Depends(accounts.require_learner_or_admin)):
    if not handlers._indexed_video(video_id, owner_id=user.get("user_id")):
        raise HTTPException(404, "Không tìm thấy video.")
    return handlers.uploaded_video_file(video_id, owner_id=user.get("user_id"))


@router.get("/api/status")
def public_status(user=Depends(accounts.require_learner_or_admin)):
    return {"ready": handlers.state["service"] is not None}


@router.post("/api/ask")
def ask(request: Request, payload: PublicAskRequest, user=Depends(accounts.require_learner_or_admin)):
    question = payload.question
    if not question.strip():
        raise HTTPException(422, "Câu hỏi không được để trống.")
    if len(question) > 500:
        raise HTTPException(422, "Câu hỏi tối đa 500 ký tự.")
    enter_ask_slot()
    try:
        check_ask_rate(auth.client_ip(request))
        result = handlers.ask(handlers.AskRequest(question=question), use_request_mode=False, owner_id=user.get("user_id"))
        sources = []
        for source in result.get("sources", []):
            item = {
                key: source[key]
                for key in ("n", "source_type", "video_id", "youtube_id", "document_id", "section", "title", "label", "start", "end", "text", "cited")
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
