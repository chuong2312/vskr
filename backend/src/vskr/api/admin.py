"""Route quản trị.

Mọi route trong ``router`` đều yêu cầu phiên admin hợp lệ (``auth.require_admin``); ``auth_pages``
chỉ chứa trang ``/admin``, tự chuyển về trang đăng nhập khi chưa đăng nhập. Thao tác thay đổi dữ
liệu đều được ghi audit.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..config import data_dir, load_config
from ..util import read_json, write_json
from fastapi.responses import FileResponse, RedirectResponse

from ..access import audit, auth, accounts
from ..config import ROOT
from . import handlers

auth_pages = APIRouter()
router = APIRouter(dependencies=[Depends(auth.require_admin)])


class VisibilityPayload(BaseModel):
    """Trạng thái quyền xem admin muốn đặt."""
    visibility: str


@router.get("/api/admin/content")
def content():
    """Mọi video và tài liệu kèm loại chủ sở hữu (khách hoặc mã người học) và trạng thái quyền xem, cho trang duyệt nội dung."""
    cfg = load_config()
    result = []
    roots = (("video", data_dir(cfg) / "videos"), ("document", data_dir(cfg) / "documents"))
    for kind, root in roots:
        if not root.is_dir():
            continue
        for meta_path in root.glob("*/meta.json"):
            try:
                meta = read_json(meta_path)
            except (OSError, ValueError):
                continue
            owner = meta.get("owner") or meta.get("owner_id")
            result.append({"kind": kind, "id": meta.get("video_id") if kind == "video" else meta.get("document_id"),
                           "title": meta.get("title"), "owner": ("guest" if str(owner).startswith("g:") else
                           ("user:" + str(owner).split(":", 1)[-1] if owner else None)),
                           "visibility": meta.get("visibility", "public" if not owner else "private"),
                           "created_at": meta.get("created_at")})
    return sorted(result, key=lambda item: item["visibility"] != "pending")


@router.post("/api/admin/content/{kind}/{item_id}/visibility")
def set_content_visibility(kind: str, item_id: str, payload: VisibilityPayload):
    """Đặt quyền xem (public, private hoặc pending) cho một học liệu."""
    if kind not in {"video", "document"} or payload.visibility not in {"public", "private", "pending"}:
        raise HTTPException(422, "Visibility không hợp lệ.")
    cfg = load_config()
    root = data_dir(cfg) / ("videos" if kind == "video" else "documents")
    meta_path = root / item_id / "meta.json"
    if not meta_path.is_file():
        raise HTTPException(404, "Không tìm thấy nội dung.")
    meta = read_json(meta_path)
    meta["visibility"] = payload.visibility
    write_json(meta_path, meta)
    service = handlers.runtime.state.get("service")
    if service is not None:
        target = service.meta if kind == "video" else service.document_meta
        target[item_id] = {**target.get(item_id, {}), **meta}
    audit.write_event("content.visibility", target_id=item_id, kind=kind, visibility=payload.visibility)
    return {"id": item_id, "visibility": payload.visibility}


@router.get("/api/admin/users")
def users():
    """Danh sách tài khoản người học."""
    return accounts.list_users()


@router.post("/api/admin/users/{user_id}/disable")
def disable_user(user_id: str):
    """Khoá tài khoản người học (mọi phiên của tài khoản bị xoá)."""
    accounts.set_user_disabled(user_id, True)
    audit.write_event("user.disable", target_id=user_id)
    return {"disabled": True}


@router.post("/api/admin/users/{user_id}/enable")
def enable_user(user_id: str):
    """Mở khoá tài khoản người học."""
    accounts.set_user_disabled(user_id, False)
    audit.write_event("user.enable", target_id=user_id)
    return {"disabled": False}


def _admin_file(relative_path: str):
    root = (ROOT / "web" / "admin").resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(path)


@auth_pages.get("/admin")
@auth_pages.get("/admin/")
def admin_home(request: Request):
    """Trang quản trị; chưa đăng nhập hoặc admin chưa cấu hình thì chuyển về trang đăng nhập."""
    try:
        auth.validate_admin_request(request)
    except HTTPException as exc:
        if exc.status_code in (401, 404):
            return RedirectResponse("/admin/login", status_code=303)
        raise
    return _admin_file("index.html")


@router.get("/admin/assets/{asset_path:path}")
def admin_asset(asset_path: str):
    """File tĩnh của trang quản trị (chỉ phục vụ sau khi đăng nhập admin)."""
    return _admin_file(asset_path)


@router.get("/api/library")
def library():
    """Toàn bộ thư viện (video và tài liệu) kèm trạng thái xử lý."""
    return handlers.library.library()


@router.get("/api/admin/jobs")
def list_jobs():
    """100 tác vụ gần nhất, kèm chi tiết lỗi đã làm sạch."""
    return handlers.jobs.list_jobs(100)


@router.post("/api/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: str):
    """Chạy lại một tác vụ bị lỗi."""
    result = handlers.jobs.retry_job(job_id)
    audit.write_event("job.retry", target_id=job_id)
    return result


@router.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    """Xoá một tác vụ đã xong hoặc bị lỗi; file nguồn chỉ bị xoá nếu học liệu chưa vào chỉ mục."""
    result = handlers.jobs.delete_job(job_id)
    audit.write_event("job.delete", target_id=job_id)
    return result


@router.delete("/api/videos/{video_id}")
def delete_video(video_id: str):
    """Xoá một video rồi dựng lại chỉ mục."""
    result = handlers.library.delete_video(video_id)
    audit.write_event("video.delete", target_id=video_id, kind="video")
    return result


@router.delete("/api/documents/{document_id}")
def delete_document(document_id: str):
    """Xoá một tài liệu rồi dựng lại chỉ mục."""
    result = handlers.library.delete_document(document_id)
    audit.write_event("document.delete", target_id=document_id, kind="document")
    return result


@router.post("/api/reload")
def reload_index():
    """Nạp lại chỉ mục từ đĩa (sau khi chạy ``vskr index``) mà không cần khởi động lại server."""
    result = handlers.runtime.reload_index()
    audit.write_event("index.reload", result="success" if result.get("ready") else "error")
    return result


@router.get("/api/admin/status")
def admin_status():
    """Trạng thái hệ thống: chỉ mục, GPU, tác vụ đang chạy và kết nối Ollama."""
    return handlers.runtime.admin_status()


@router.post("/api/admin/ask")
def admin_ask(payload: handlers.ask.AskRequest):
    """Hỏi đáp của admin: thấy mọi học liệu và được chọn chế độ tìm kiếm."""
    return handlers.ask.ask(payload, admin=True)


@router.get("/api/eval/questions")
def get_eval_questions():
    """Bộ câu hỏi đánh giá hiện tại."""
    return handlers.evaluation.get_eval_questions()


@router.get("/api/eval/results")
def get_eval_results():
    """Kết quả đánh giá lần gần nhất; ``stale`` cho biết bộ câu hỏi đã đổi kể từ lần chạy đó."""
    return handlers.evaluation.get_eval_results()


@router.post("/api/eval/questions")
def save_eval_questions(payload: dict):
    """Thêm câu hỏi đánh giá (một câu hoặc một danh sách)."""
    result = handlers.evaluation.save_eval_questions(payload)
    audit.write_event("evaluation.questions.save")
    return result


@router.post("/api/eval/questions/import")
async def import_eval_questions(request: Request):
    """Nhập câu hỏi đánh giá từ tệp JSONL (tối đa ``limits.eval_import_bytes``)."""
    async with request.form(max_files=1, max_fields=0, max_part_size=int(load_config()["limits"]["eval_import_bytes"])) as form:
        file = form.get("file")
        if file is None or not hasattr(file, "read"):
            raise HTTPException(422, "Cần tải lên một tệp JSONL.")
        result = await handlers.evaluation.import_eval_questions(file=file)
    audit.write_event("evaluation.questions.import")
    return result


@router.delete("/api/eval/questions/{question_id}")
def delete_eval_question(question_id: str):
    """Xoá một câu hỏi đánh giá; kết quả đánh giá cũ bị xoá theo."""
    result = handlers.evaluation.delete_eval_question(question_id)
    audit.write_event("evaluation.question.delete", target_id=question_id)
    return result


@router.post("/api/eval/run")
def run_retrieval_evaluation(options: handlers.evaluation.RetrievalExperiment = handlers.evaluation.RetrievalExperiment()):
    """Chạy đánh giá truy hồi cho cả 5 chế độ, cùng các thử nghiệm graph nếu được bật."""
    try:
        result = handlers.evaluation.run_retrieval_evaluation(options)
    except Exception:
        audit.write_event("evaluation.run", result="error")
        raise
    audit.write_event("evaluation.run", result="success")
    return result


@router.get("/api/graph")
def inspect_graph(query: str, adjacent: bool = False, idf_weighting: bool = False,
                  semantic_expansion: bool = False, ppr: bool = False,
                  damping: float = 0.5, seed_weight: float = 0.05):
    """Xem các đoạn và khái niệm mà truy vấn graph tìm được cho một câu hỏi."""
    return handlers.graph.inspect_graph(query, adjacent, idf_weighting, semantic_expansion, ppr, damping, seed_weight)


@router.get("/api/audit")
def audit_events():
    """200 sự kiện audit gần nhất."""
    return audit.recent_events(200)
