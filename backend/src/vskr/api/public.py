"""Learner app, learner authentication, and the deliberately small admin login surface."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .. import accounts, audit, auth
from ..config import ROOT
from . import handlers
from .limits import check_ask_rate, enter_ask_slot, leave_ask_slot, rollback_ask_rate
from .ssrf import validate_learner_ingest_url

router = APIRouter()


class PublicAskRequest(BaseModel):
    question: str
    conversation_id: str | None = Field(default=None, max_length=64)


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=72)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


def _form_upload(form, name: str, *, required: bool = False):
    item = form.get(name)
    if item is None:
        if required:
            raise HTTPException(422, "Hãy chọn một tệp để tải lên.")
        return None
    if not hasattr(item, "read") or not hasattr(item, "filename"):
        raise HTTPException(400, "Trường tải lên cần là một tệp.")
    return item


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
def user_home():
    return FileResponse(ROOT / "web" / "user" / "index.html")


@router.get("/login")
def user_login_page():
    return FileResponse(
        ROOT / "web" / "user" / "login.html",
        headers={"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store"},
    )


@router.get("/register")
def user_register_page():
    return FileResponse(
        ROOT / "web" / "user" / "login.html",
        headers={"X-Robots-Tag": "noindex, nofollow", "Cache-Control": "no-store"},
    )


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(
        ROOT / "web" / "user" / "icons" / "icon.svg",
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/api/user/auth/config")
def user_auth_config():
    return {"registration_enabled": True}


@router.post("/api/user/auth/register")
def user_register(payload: LoginRequest, request: Request):
    if not auth._secure_cookie_request(request) or not auth._same_origin(request):
        raise HTTPException(403, "Yêu cầu đăng ký không hợp lệ.")
    ip = auth.client_ip(request)
    accounts.check_registration_rate(ip)
    retry = auth.login_retry_after(ip, payload.username)
    if retry:
        raise HTTPException(429, "Thử lại đăng ký sau.", headers={"Retry-After": str(retry)})
    user = accounts.register(payload.username, payload.password)
    auth._clear_login_failures(ip, payload.username)
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
def public_videos(user=Depends(accounts.optional_learner_or_admin)):
    return handlers.public_videos(owner_id=user.get("user_id"), shared_only=user.get("guest", False))


@router.get("/api/catalog")
def public_catalog(user=Depends(accounts.optional_learner_or_admin)):
    return handlers.public_catalog(owner_id=user.get("user_id"), shared_only=user.get("guest", False))


@router.post("/api/ingest", status_code=202)
async def public_ingest(request: Request, user=Depends(accounts.require_learner_or_admin)):
    async with request.form(max_files=1, max_fields=1, max_part_size=64 * 1024) as form:
        file = _form_upload(form, "file")
        url = form.get("url")
        if url is not None and not isinstance(url, str):
            raise HTTPException(400, "Trường URL không hợp lệ.")
        if url and not user.get("admin"):
            url = validate_learner_ingest_url(url)
        result = await handlers.start_ingest(url=url, file=file, owner_id=user.get("user_id"))
    audit.write_event("ingest.start", target_id=result.get("job_id"), kind="upload" if file else "url")
    return result


@router.post("/api/documents", status_code=202)
async def public_upload_document(request: Request, user=Depends(accounts.require_learner_or_admin)):
    async with request.form(max_files=1, max_fields=0, max_part_size=64 * 1024) as form:
        file = _form_upload(form, "file", required=True)
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
def public_document_section(document_id: str, section: int, user=Depends(accounts.optional_learner_or_admin)):
    owner_id = user.get("user_id")
    shared_only = user.get("guest", False)
    if not handlers._indexed_document(document_id, owner_id=owner_id, shared_only=shared_only):
        raise HTTPException(404, "Không tìm thấy mục tài liệu.")
    return handlers.document_section(document_id, section, owner_id=owner_id, shared_only=shared_only)


@router.get("/api/videos/{video_id}/file")
def public_video_file(video_id: str, user=Depends(accounts.optional_learner_or_admin)):
    owner_id = user.get("user_id")
    shared_only = user.get("guest", False)
    if not handlers._indexed_video(video_id, owner_id=owner_id, shared_only=shared_only):
        raise HTTPException(404, "Không tìm thấy video.")
    return handlers.uploaded_video_file(video_id, owner_id=owner_id, shared_only=shared_only)


@router.get("/api/status")
def public_status(user=Depends(accounts.optional_learner_or_admin)):
    return {"ready": handlers.state["service"] is not None}


@router.get("/api/conversations")
def list_conversations(user=Depends(accounts.require_user)):
    return accounts.list_conversations(user["user_id"])


@router.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str, user=Depends(accounts.require_user)):
    return accounts.get_conversation(user["user_id"], conversation_id)


@router.patch("/api/conversations/{conversation_id}")
def rename_conversation(conversation_id: str, payload: RenameConversationRequest,
                        user=Depends(accounts.require_user)):
    return accounts.rename_conversation(user["user_id"], conversation_id, payload.title)


@router.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, user=Depends(accounts.require_user)):
    accounts.delete_conversation(user["user_id"], conversation_id)
    return {"deleted": True}


@router.post("/api/ask")
def ask(request: Request, payload: PublicAskRequest, user=Depends(accounts.optional_learner_or_admin)):
    question = payload.question
    if not question.strip():
        raise HTTPException(422, "Câu hỏi không được để trống.")
    if len(question) > 500:
        raise HTTPException(422, "Câu hỏi tối đa 500 ký tự.")
    enter_ask_slot()
    client_ip = auth.client_ip(request)
    attempt_id = None
    try:
        attempt_id = check_ask_rate(client_ip)
        result = handlers.ask(
            handlers.AskRequest(question=question), use_request_mode=False,
            owner_id=user.get("user_id"), shared_only=user.get("guest", False),
        )
        sources = []
        for source in result.get("sources", []):
            item = {
                key: source[key]
                for key in ("n", "source_type", "video_id", "youtube_id", "document_id", "section", "title", "label", "start", "end", "text", "cited")
                if key in source
            }
            item["link"] = _safe_source_link(source)
            sources.append(item)
        response = {"answer": result["answer"], "found": result["found"], "cited": result["cited"], "sources": sources}
        if user.get("user_id"):
            try:
                response["conversation_id"] = accounts.save_exchange(
                    user["user_id"], payload.conversation_id, question, response
                )
                response["history_saved"] = True
            except sqlite3.Error:
                logging.exception("Could not save learner conversation history")
                response["history_saved"] = False
                response["history_message"] = "Câu trả lời đã có, nhưng chưa lưu được lịch sử. Hãy thử lại sau."
        return response
    except HTTPException as exc:
        if exc.status_code >= 500:
            rollback_ask_rate(client_ip, attempt_id)
        else:
            logging.info("Public ask returned status %s", exc.status_code)
        raise
    except Exception:
        rollback_ask_rate(client_ip, attempt_id)
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
