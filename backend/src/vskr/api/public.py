"""Learner app, learner authentication, and the deliberately small admin login surface."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from ..access import accounts, audit, auth
from ..config import ROOT, data_dir, load_config
from ..util import read_json, write_json
from . import handlers
from .limits import check_ask_rate, enter_ask_slot, leave_ask_slot, rollback_ask_rate
from ..pipeline.url_safety import validate_learner_ingest_url

router = APIRouter()


class PublicAskRequest(BaseModel):
    question: str
    conversation_id: str | None = Field(default=None, max_length=64)
    source_id: str | None = Field(default=None, max_length=256)
    source_type: str = Field(default="library", max_length=16)
    scope_type: str | None = Field(default=None, max_length=16)
    project_id: str | None = Field(default=None, max_length=64)


class CreateConversationRequest(BaseModel):
    title: str = Field(default="Cuộc trò chuyện mới", max_length=72)
    source_id: str | None = Field(default=None, max_length=256)
    source_type: str = Field(default="library", max_length=16)
    scope_type: str | None = Field(default=None, max_length=16)
    project_id: str | None = Field(default=None, max_length=64)


class RenameConversationRequest(BaseModel):
    title: str = Field(min_length=1, max_length=72)


class VisibilityPayload(BaseModel):
    visibility: str


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class LoginRequest(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=256)
    email: str | None = Field(default=None, min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)

    @property
    def identifier(self):
        return (self.email or self.username or "").strip().lower()


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


def _rate_limit_new_guest_identity(request: Request, user: dict, *, guest_write: bool = False) -> None:
    if user.get("guest") and (guest_write or getattr(request.state, "new_guest_token", None)):
        accounts.check_guest_identity_rate(auth.client_ip(request))


@router.get("/")
def user_home():
    return FileResponse(ROOT / "web" / "user" / "index.html", headers={"Cache-Control": "no-cache"})


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
@router.post("/api/user/register")
def user_register(payload: LoginRequest, request: Request):
    if not auth._secure_cookie_request(request) or not auth._same_origin(request):
        raise HTTPException(403, "Yêu cầu đăng ký không hợp lệ.")
    ip = auth.client_ip(request)
    accounts.check_registration_rate(ip)
    identifier = payload.identifier
    retry = auth.login_retry_after(ip, identifier, scope="learner")
    if retry:
        raise HTTPException(429, "Thử lại đăng ký sau.", headers={"Retry-After": str(retry)})
    user = accounts.register(identifier, payload.password)
    auth._clear_login_failures(ip, identifier, scope="learner")
    token, session = accounts.create_session(identifier, payload.password)
    response = JSONResponse({"user": {"user_id": user["user_id"], "username": user["username"]}, "csrf_token": session["csrf_token"]})
    accounts.set_cookie(response, token)
    return response


@router.post("/api/user/auth/login")
@router.post("/api/user/login")
def user_login(payload: LoginRequest, request: Request):
    if not auth._secure_cookie_request(request) or not auth._same_origin(request):
        raise HTTPException(403, "Yêu cầu đăng nhập không hợp lệ.")
    ip = auth.client_ip(request)
    identifier = payload.identifier
    retry = auth.login_retry_after(ip, identifier, scope="learner")
    if retry:
        raise HTTPException(429, "Sai tên đăng nhập hoặc mật khẩu.", headers={"Retry-After": str(retry)})
    try:
        token, session = accounts.create_session(identifier, payload.password)
    except HTTPException as exc:
        if exc.status_code == 401:
            auth._record_login_failure(ip, identifier, scope="learner")
        raise
    auth._clear_login_failures(ip, identifier, scope="learner")
    response = JSONResponse({"user": {"user_id": session["user_id"], "username": session["username"]}, "csrf_token": session["csrf_token"]})
    accounts.set_cookie(response, token)
    return response


@router.post("/api/user/auth/logout")
@router.post("/api/user/logout")
def user_logout(request: Request, user=Depends(accounts.require_user)):
    accounts.revoke_session(request.cookies.get(accounts.COOKIE_NAME))
    response = JSONResponse({"ok": True})
    accounts.clear_cookie(response)
    return response


@router.get("/api/user/auth/me")
def user_me(user=Depends(accounts.require_user)):
    return {"user": {"id": user["user_id"], "email": user["username"],
                     "display_name": user["username"]},
            "user_id": user["user_id"], "username": user["username"],
            "csrf_token": user["csrf_token"],
            "limits": {"max_upload_mb": 1024, "max_duration_min": 120,
                       "max_active_jobs": 2}}


@router.get("/api/user/me")
def user_me_contract(request: Request):
    token = request.cookies.get(accounts.COOKIE_NAME)
    user = accounts.get_session(token)
    if not user:
        return {"user": None, "csrf_token": None,
                "limits": {"max_upload_mb": 100, "max_duration_min": 15,
                           "max_active_jobs": 1}}
    return user_me(user)


@router.get("/user/assets/{asset_path:path}")
def user_asset(asset_path: str):
    response = _file_response(ROOT / "web" / "user", asset_path, "Không tìm thấy tài nguyên.")
    response.headers["Cache-Control"] = "no-cache"
    return response


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
    return handlers.public_videos(owner_id=user.get("owner") or user.get("user_id"),
                                  shared_only=bool(user.get("guest")))


@router.get("/api/projects")
def list_projects(request: Request, user=Depends(accounts.require_learner_or_guest)):
    _rate_limit_new_guest_identity(request, user)
    owner_id = user.get("owner") or user.get("user_id")
    result = accounts.list_projects(owner_id)
    response = JSONResponse(result)
    if user.get("guest_token"):
        accounts.set_guest_cookie(response, user["guest_token"])
    return response


@router.post("/api/projects")
def create_project(payload: CreateProjectRequest, request: Request,
                   user=Depends(accounts.require_learner_or_guest)):
    _rate_limit_new_guest_identity(request, user, guest_write=True)
    response = JSONResponse(accounts.create_project(user.get("owner") or user.get("user_id"), payload.name))
    if user.get("guest_token"):
        accounts.set_guest_cookie(response, user["guest_token"])
    return response


@router.get("/api/catalog")
def public_catalog(scope: str | None = Query(default=None),
                   project_id: str | None = Query(default=None, max_length=64),
                   user=Depends(accounts.optional_learner_or_admin)):
    owner_id = user.get("owner") or user.get("user_id")
    if project_id:
        accounts.get_project(owner_id, project_id)
    if scope == "mine":
        if not user.get("user_id"):
            return {"videos": [], "documents": []}
        return handlers.public_catalog(owner_id=user["user_id"], owned_only=True, project_id=project_id)
    if scope == "public":
        return handlers.public_catalog(owner_id=owner_id, shared_only=True, project_id=project_id)
    if scope is not None:
        raise HTTPException(422, "Phạm vi thư viện không hợp lệ.")
    return handlers.public_catalog(owner_id=owner_id, shared_only=bool(user.get("guest")),
                                   project_id=project_id)


@router.post("/api/ingest", status_code=202)
async def public_ingest(request: Request, user=Depends(accounts.require_learner_or_guest)):
    _rate_limit_new_guest_identity(request, user, guest_write=True)
    owner_id = user.get("owner") or user.get("user_id")
    async with request.form(max_files=1, max_fields=2, max_part_size=64 * 1024) as form:
        project = accounts.get_project(owner_id, form.get("project_id"))
        file = _form_upload(form, "file")
        url = form.get("url")
        if url is not None and not isinstance(url, str):
            raise HTTPException(400, "Trường URL không hợp lệ.")
        if url:
            cfg = load_config()
            if user.get("guest") and (
                not cfg.get("ingest", {}).get("guest_url_ingest_enabled", False)
                or not auth.egress_firewall_confirmed()
            ):
                raise HTTPException(
                    403,
                    "Khách chưa được phép ingest URL; cần bật guest_url_ingest_enabled "
                    "và xác nhận egress firewall trước.",
                )
            url = validate_learner_ingest_url(
                url, allowed_domains=cfg.get("ingest", {}).get("allowed_domains")
            )
        result = await handlers.start_ingest(url=url, file=file,
                                             owner_id=owner_id, project_id=project["project_id"])
    audit.write_event("ingest.start", target_id=result.get("job_id"), kind="upload" if file else "url")
    response = JSONResponse(result, status_code=202)
    if user.get("guest_token"):
        accounts.set_guest_cookie(response, user["guest_token"])
    return response


@router.post("/api/documents", status_code=202)
async def public_upload_document(request: Request, user=Depends(accounts.require_learner_or_guest)):
    _rate_limit_new_guest_identity(request, user, guest_write=True)
    owner_id = user.get("owner") or user.get("user_id")
    async with request.form(max_files=1, max_fields=2, max_part_size=64 * 1024) as form:
        project = accounts.get_project(owner_id, form.get("project_id"))
        file = _form_upload(form, "file", required=True)
        result = await handlers.upload_document(file=file,
                                                owner_id=owner_id, project_id=project["project_id"])
    audit.write_event("document.upload", target_id=result.get("job_id"), kind="document")
    response = JSONResponse(result, status_code=202)
    if user.get("guest_token"):
        accounts.set_guest_cookie(response, user["guest_token"])
    return response


@router.get("/api/jobs/{job_id}")
def public_job_status(job_id: str, user=Depends(accounts.optional_learner_or_admin)):
    job = handlers.get_job(job_id)
    owner_id = user.get("owner") or user.get("user_id")
    if not user.get("admin") and job.get("owner_id") != owner_id:
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
def list_conversations(project_id: str | None = Query(default=None, max_length=64),
                        user=Depends(accounts.require_user)):
    if project_id:
        accounts.get_project(user["user_id"], project_id)
    return accounts.list_conversations(user["user_id"], project_id)


@router.post("/api/conversations")
def create_conversation(payload: CreateConversationRequest, user=Depends(accounts.require_user)):
    scope_type = payload.scope_type or ("library" if payload.source_type == "library" else "source")
    if scope_type not in {"source", "library"}:
        raise HTTPException(422, "Phạm vi cuộc trò chuyện không hợp lệ.")
    if payload.source_type not in {"video", "document", "library"}:
        raise HTTPException(422, "Loại nguồn không hợp lệ.")
    source_type = payload.source_type if scope_type == "source" else "library"
    source_id = payload.source_id if scope_type == "source" else None
    if scope_type == "source" and not handlers.source_is_visible(source_type, source_id, user["user_id"]):
        raise HTTPException(404, "Không tìm thấy nguồn.")
    project = (accounts.get_project(user["user_id"], payload.project_id)
               if payload.project_id else accounts.get_or_create_default_project(user["user_id"]))
    return accounts.create_conversation(user["user_id"], payload.title, source_id,
                                       source_type, project["project_id"], scope_type)


@router.get("/api/conversations/{conversation_id}")
def get_conversation(conversation_id: str, user=Depends(accounts.require_user)):
    conversation = accounts.get_conversation(user["user_id"], conversation_id)
    conversation["source_deleted"] = (
        conversation["scope_type"] == "source"
        and not handlers.source_is_visible(
            conversation["source_type"], conversation["source_id"], user["user_id"]
        )
    )
    return conversation


@router.patch("/api/conversations/{conversation_id}")
def rename_conversation(conversation_id: str, payload: RenameConversationRequest,
                        user=Depends(accounts.require_user)):
    return accounts.rename_conversation(user["user_id"], conversation_id, payload.title)


@router.delete("/api/conversations/{conversation_id}")
def delete_conversation(conversation_id: str, user=Depends(accounts.require_user)):
    accounts.delete_conversation(user["user_id"], conversation_id)
    return {"deleted": True}


@router.get("/api/my/jobs")
def my_jobs(user=Depends(accounts.require_learner_or_guest)):
    owner = user.get("owner") or user.get("user_id")
    fields = ("job_id", "status", "phase", "message", "title", "video_id", "document_id", "project_id")
    return [
        {key: job[key] for key in fields if key in job}
        for job in handlers.list_jobs(200)
        if job.get("owner_id") == owner
    ]


@router.delete("/api/my/content/{kind}/{item_id}")
def delete_my_content(kind: str, item_id: str, user=Depends(accounts.require_learner_or_guest)):
    cfg = load_config()
    root = data_dir(cfg) / ("videos" if kind == "video" else "documents")
    meta_path = root / item_id / "meta.json"
    if kind not in {"video", "document"} or not meta_path.is_file():
        raise HTTPException(404, "Không tìm thấy nội dung.")
    meta = read_json(meta_path)
    owner = user.get("owner") or user.get("user_id")
    if (meta.get("owner") or meta.get("owner_id")) != owner:
        raise HTTPException(404, "Không tìm thấy nội dung.")
    return handlers.delete_video(item_id) if kind == "video" else handlers.delete_document(item_id)


@router.post("/api/my/content/{kind}/{item_id}/visibility")
def request_visibility(kind: str, item_id: str, payload: VisibilityPayload,
                       user=Depends(accounts.require_learner_or_guest)):
    if payload.visibility not in {"private", "pending"}:
        raise HTTPException(422, "Chỉ có thể đặt private hoặc pending.")
    cfg = load_config()
    root = data_dir(cfg) / ("videos" if kind == "video" else "documents")
    meta_path = root / item_id / "meta.json"
    if kind not in {"video", "document"} or not meta_path.is_file():
        raise HTTPException(404, "Không tìm thấy nội dung.")
    meta = read_json(meta_path)
    if (meta.get("owner") or meta.get("owner_id")) != (user.get("owner") or user.get("user_id")):
        raise HTTPException(404, "Không tìm thấy nội dung.")
    meta["visibility"] = payload.visibility
    write_json(meta_path, meta)
    service = handlers.state.get("service")
    if service is not None:
        target = service.meta if kind == "video" else service.document_meta
        target[item_id] = {**target.get(item_id, {}), **meta}
    return {"id": item_id, "visibility": payload.visibility}


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
        scope_type = payload.scope_type or ("library" if payload.source_type == "library" else "source")
        source_id = payload.source_id
        source_type = payload.source_type
        if payload.conversation_id and user.get("user_id"):
            conversation = accounts.get_conversation(user["user_id"], payload.conversation_id)
            source_id = conversation["source_id"]
            source_type = conversation["source_type"]
            scope_type = conversation["scope_type"]
            project_id = conversation["project_id"]
        else:
            project_id = payload.project_id
        if project_id and user.get("user_id"):
            accounts.get_project(user["user_id"], project_id)
        if scope_type not in {"source", "library"}:
            raise HTTPException(422, "Phạm vi cuộc trò chuyện không hợp lệ.")
        if source_type not in {"video", "document", "library"}:
            raise HTTPException(422, "Loại nguồn không hợp lệ.")
        owner_id = user.get("owner") or user.get("user_id")
        shared_only = False
        if scope_type == "source" and not handlers.source_is_visible(
                source_type, source_id, owner_id, shared_only=shared_only):
            if payload.conversation_id and user.get("user_id"):
                raise HTTPException(410, detail={
                    "message": "Nguồn của cuộc trò chuyện đã bị xoá.",
                    "source_deleted": True,
                })
            raise HTTPException(404, "Không tìm thấy nguồn.")
        if scope_type == "source":
            allowed_sources = {source_id}
        elif shared_only:
            allowed_sources = None
        else:
            allowed_sources = None
        result = handlers.ask(
            handlers.AskRequest(question=question), use_request_mode=False, mode_override="hybrid",
            owner_id=owner_id, shared_only=shared_only, allowed_sources=allowed_sources,
            project_id=None,
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
        no_sources = result.get("no_sources")
        response = {
            "answer": "Nội dung này không có trong nguồn đang chọn" if no_sources else result["answer"],
            "found": result["found"], "cited": False if no_sources else result["cited"],
            "sources": [] if no_sources else sources,
        }
        if no_sources is not None:
            response["no_sources"] = bool(no_sources)
        if user.get("user_id"):
            try:
                response["conversation_id"] = accounts.save_exchange(
                    user["user_id"], payload.conversation_id, question, response,
                    source_id=source_id, source_type=source_type, scope_type=scope_type, project_id=project_id,
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
