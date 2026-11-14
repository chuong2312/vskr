"""Authenticated admin UI and management routes."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel

from .. import audit, auth
from ..config import ROOT
from .. import state_store
from . import graph as graph_api, jobs as jobs_api, library as library_api
from .handlers import AskRequest, ask
from .evaluation import (
    RetrievalExperiment, delete_eval_question as remove_eval_question,
    get_eval_questions as read_eval_questions, get_eval_results as read_eval_results,
    import_eval_questions as import_questions, run_retrieval_evaluation as run_evaluation,
    save_eval_questions as write_eval_questions,
)

router = APIRouter()
protected = APIRouter(dependencies=[Depends(auth.require_admin)])


class VisibilityRequest(BaseModel):
    is_public: bool


def _admin_file(relative_path: str):
    root = (ROOT / "web" / "admin").resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(path)


@router.get("/admin")
@router.get("/admin/")
def admin_home(request: Request):
    try:
        auth.validate_admin_request(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            return RedirectResponse("/admin/login", status_code=303)
        raise
    return _admin_file("index.html")


@router.get("/admin/assets/{asset_path:path}", dependencies=[Depends(auth.require_admin)])
def admin_asset(asset_path: str):
    return _admin_file(asset_path)


@protected.get("/api/library")
def library():
    return library_api.library()


@protected.patch("/api/library/{kind}/{item_id}/visibility")
def set_visibility(kind: str, item_id: str, payload: VisibilityRequest):
    return library_api.set_library_visibility(kind, item_id, payload.is_public)


@protected.get("/api/admin/questions")
def question_logs():
    return state_store.recent_questions(200)


@protected.post("/api/ingest", status_code=202)
async def start_ingest(url: str | None = Form(None), file: UploadFile | None = File(None)):
    result = await jobs_api.start_ingest(url=url, file=file)
    audit.write_event("ingest.start", target_id=result.get("job_id"), kind="upload" if file else "url")
    return result


@protected.post("/api/documents", status_code=202)
async def upload_document(file: UploadFile = File(...)):
    result = await jobs_api.upload_document(file=file)
    audit.write_event("document.upload", target_id=result.get("job_id"), kind="document")
    return result


@protected.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    return jobs_api.get_job(job_id)


@protected.get("/api/admin/jobs")
def list_jobs():
    return jobs_api.list_jobs(100)


@protected.post("/api/jobs/{job_id}/retry", status_code=202)
def retry_job(job_id: str):
    result = jobs_api.retry_job(job_id)
    audit.write_event("job.retry", target_id=job_id)
    return result


@protected.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    result = library_api.delete_job(job_id)
    audit.write_event("job.delete", target_id=job_id)
    return result


@protected.delete("/api/videos/{video_id}")
def delete_video(video_id: str):
    result = library_api.delete_video(video_id)
    audit.write_event("video.delete", target_id=video_id, kind="video")
    return result


@protected.delete("/api/documents/{document_id}")
def delete_document(document_id: str):
    result = library_api.delete_document(document_id)
    audit.write_event("document.delete", target_id=document_id, kind="document")
    return result


@protected.post("/api/reload")
def reload_index():
    result = jobs_api.reload_index()
    audit.write_event("index.reload", result="success" if result.get("ready") else "error")
    return result


@protected.get("/api/admin/status")
def admin_status():
    return library_api.admin_status()


@protected.post("/api/admin/ask")
def admin_ask(payload: AskRequest):
    return ask(payload, admin=True)


@protected.get("/api/eval/questions")
def get_eval_questions():
    return read_eval_questions()


@protected.get("/api/eval/results")
def get_eval_results():
    return read_eval_results()


@protected.post("/api/eval/questions")
def save_eval_questions(payload: dict):
    result = write_eval_questions(payload)
    audit.write_event("evaluation.questions.save")
    return result


@protected.post("/api/eval/questions/import")
async def import_eval_questions(file: UploadFile = File(...)):
    result = await import_questions(file=file)
    audit.write_event("evaluation.questions.import")
    return result


@protected.delete("/api/eval/questions/{question_id}")
def delete_eval_question(question_id: str):
    result = remove_eval_question(question_id)
    audit.write_event("evaluation.question.delete", target_id=question_id)
    return result


@protected.post("/api/eval/run")
def run_retrieval_evaluation(options: RetrievalExperiment = RetrievalExperiment()):
    try:
        result = run_evaluation(options)
    except Exception:
        audit.write_event("evaluation.run", result="error")
        raise
    audit.write_event("evaluation.run", result="success")
    return result


@protected.get("/api/graph")
def inspect_graph(query: str, adjacent: bool = False, idf_weighting: bool = False,
                  semantic_expansion: bool = False, ppr: bool = False,
                  damping: float = 0.5, seed_weight: float = 0.05):
    from .runtime import init_service, service_lease, state

    if state["service"] is None:
        init_service()
    with service_lease() as service:
        if service is None:
            raise HTTPException(503, "Dịch vụ chưa sẵn sàng. Hãy kiểm tra nhật ký máy chủ.")
        return graph_api.inspect_graph(
            service, query, adjacent, idf_weighting, semantic_expansion, ppr, damping, seed_weight
        )


@protected.get("/api/audit")
def audit_events():
    return audit.recent_events(200)


router.include_router(protected)
