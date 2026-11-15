"""Authenticated admin UI and management routes."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse

from .. import audit, auth, accounts
from ..config import ROOT
from . import handlers

auth_pages = APIRouter()
router = APIRouter(dependencies=[Depends(auth.require_admin)])


@router.get("/api/admin/users")
def users():
    return accounts.list_users()


@router.post("/api/admin/users/{user_id}/disable")
def disable_user(user_id: str):
    accounts.set_user_disabled(user_id, True)
    audit.write_event("user.disable", target_id=user_id)
    return {"disabled": True}


@router.post("/api/admin/users/{user_id}/enable")
def enable_user(user_id: str):
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
    try:
        auth.validate_admin_request(request)
    except HTTPException as exc:
        if exc.status_code in (401, 404):
            return RedirectResponse("/admin/login", status_code=303)
        raise
    return _admin_file("index.html")


@router.get("/admin/assets/{asset_path:path}")
def admin_asset(asset_path: str):
    return _admin_file(asset_path)


@router.get("/api/library")
def library():
    return handlers.library()


@router.get("/api/admin/jobs")
def list_jobs():
    return handlers.list_jobs(100)


@router.post("/api/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: str):
    result = handlers.retry_job(job_id)
    audit.write_event("job.retry", target_id=job_id)
    return result


@router.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    result = handlers.delete_job(job_id)
    audit.write_event("job.delete", target_id=job_id)
    return result


@router.delete("/api/videos/{video_id}")
def delete_video(video_id: str):
    result = handlers.delete_video(video_id)
    audit.write_event("video.delete", target_id=video_id, kind="video")
    return result


@router.delete("/api/documents/{document_id}")
def delete_document(document_id: str):
    result = handlers.delete_document(document_id)
    audit.write_event("document.delete", target_id=document_id, kind="document")
    return result


@router.post("/api/reload")
def reload_index():
    result = handlers.reload_index()
    audit.write_event("index.reload", result="success" if result.get("ready") else "error")
    return result


@router.get("/api/admin/status")
def admin_status():
    return handlers.admin_status()


@router.post("/api/admin/ask")
def admin_ask(payload: handlers.AskRequest):
    return handlers.ask(payload, admin=True)


@router.get("/api/eval/questions")
def get_eval_questions():
    return handlers.get_eval_questions()


@router.get("/api/eval/results")
def get_eval_results():
    return handlers.get_eval_results()


@router.post("/api/eval/questions")
def save_eval_questions(payload: dict):
    result = handlers.save_eval_questions(payload)
    audit.write_event("evaluation.questions.save")
    return result


@router.post("/api/eval/questions/import")
async def import_eval_questions(request: Request):
    async with request.form(max_files=1, max_fields=0, max_part_size=5 * 1024 * 1024) as form:
        file = form.get("file")
        if file is None or not hasattr(file, "read"):
            raise HTTPException(422, "Cần tải lên một tệp JSONL.")
        result = await handlers.import_eval_questions(file=file)
    audit.write_event("evaluation.questions.import")
    return result


@router.delete("/api/eval/questions/{question_id}")
def delete_eval_question(question_id: str):
    result = handlers.delete_eval_question(question_id)
    audit.write_event("evaluation.question.delete", target_id=question_id)
    return result


@router.post("/api/eval/run")
def run_retrieval_evaluation(options: handlers.RetrievalExperiment = handlers.RetrievalExperiment()):
    try:
        result = handlers.run_retrieval_evaluation(options)
    except Exception:
        audit.write_event("evaluation.run", result="error")
        raise
    audit.write_event("evaluation.run", result="success")
    return result


@router.get("/api/graph")
def inspect_graph(query: str, adjacent: bool = False, idf_weighting: bool = False,
                  semantic_expansion: bool = False, ppr: bool = False,
                  damping: float = 0.5, seed_weight: float = 0.05):
    return handlers.inspect_graph(query, adjacent, idf_weighting, semantic_expansion, ppr, damping, seed_weight)


@router.get("/api/audit")
def audit_events():
    return audit.recent_events(200)
