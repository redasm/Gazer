from __future__ import annotations

"""Cron scheduler management router."""

from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException

from .state import get_cron_scheduler
from .auth import verify_admin_token

router = APIRouter(tags=["cron"])


@router.get("/cron", dependencies=[Depends(verify_admin_token)])
async def list_cron_jobs():
    """List all cron jobs."""
    cron = get_cron_scheduler()
    if cron is None:
        raise HTTPException(status_code=503, detail="Cron scheduler not available")
    from dataclasses import asdict
    jobs = [asdict(j) for j in cron.list_jobs()]
    return {"jobs": jobs}


@router.post("/cron", dependencies=[Depends(verify_admin_token)])
async def create_cron_job(data: Dict[str, Any]):
    """Create a new cron job."""
    cron = get_cron_scheduler()
    if cron is None:
        raise HTTPException(status_code=503, detail="Cron scheduler not available")
    from scheduler.cron import CronJob
    name = data.get("name", "Unnamed")
    cron_expr = data.get("cron_expr", "")
    message = data.get("message", "")
    if not cron_expr or not message:
        raise HTTPException(status_code=400, detail="cron_expr and message are required")
    job = CronJob(
        name=name,
        cron_expr=cron_expr,
        message=message,
        enabled=data.get("enabled", True),
        one_shot=data.get("one_shot", False),
    )
    cron.add(job)
    from dataclasses import asdict
    return {"status": "success", "job": asdict(job)}


@router.delete("/cron/{job_id}", dependencies=[Depends(verify_admin_token)])
async def delete_cron_job(job_id: str):
    """Delete a cron job."""
    cron = get_cron_scheduler()
    if cron is None:
        raise HTTPException(status_code=503, detail="Cron scheduler not available")
    if not cron.remove(job_id):
        raise HTTPException(status_code=404, detail="Job not found")
    return {"status": "success", "job_id": job_id}


@router.put("/cron/{job_id}", dependencies=[Depends(verify_admin_token)])
async def update_cron_job(job_id: str, data: Dict[str, Any]):
    """Update a cron job."""
    cron = get_cron_scheduler()
    if cron is None:
        raise HTTPException(status_code=503, detail="Cron scheduler not available")
    updates = {k: v for k, v in data.items() if k in ("name", "cron_expr", "message", "enabled", "one_shot")}
    job = cron.edit(job_id, **updates)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    from dataclasses import asdict
    return {"status": "success", "job": asdict(job)}
