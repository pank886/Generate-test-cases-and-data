"""compensation_tasks 表 CRUD 操作。"""

import json as _json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from database.models import CompensationTask


class CompensationOps:
    """补偿任务 CRUD（全部 @staticmethod，session 为第一参数）。"""

    @staticmethod
    def create(session: Session, task_type: str, payload: dict,
               max_retries: int = 3) -> CompensationTask:
        """创建补偿任务。

        Args:
            task_type: simple_summary / analyzed_summary / chroma_rebuild / api_search_text
            payload: 任务参数 dict，如 {"doc_id": "xxx", "chunk_indices": [0,1,2]}
            max_retries: 最大重试次数，默认 3
        """
        task = CompensationTask(
            task_type=task_type,
            payload=_json.dumps(payload, ensure_ascii=False),
            status="pending",
            retry_count=0,
            max_retries=max_retries,
        )
        session.add(task)
        session.flush()
        return task

    @staticmethod
    def fetch_pending(session: Session, task_type: str = None,
                       limit: int = 10) -> list[CompensationTask]:
        """获取待处理任务（按创建时间升序，FIFO）。

        Args:
            task_type: 可选，按类型过滤
            limit: 每次取出的最大数量
        """
        q = session.query(CompensationTask).filter_by(status="pending")
        if task_type:
            q = q.filter_by(task_type=task_type)
        return q.order_by(CompensationTask.created_at.asc()).limit(limit).all()

    @staticmethod
    def mark_running(session: Session, task: CompensationTask):
        """标记任务为执行中。"""
        task.status = "running"
        task.updated_at = datetime.now(timezone.utc)
        session.flush()

    @staticmethod
    def mark_success(session: Session, task: CompensationTask):
        """标记任务成功。"""
        task.status = "success"
        task.updated_at = datetime.now(timezone.utc)
        session.flush()

    @staticmethod
    def mark_failed(session: Session, task: CompensationTask, error_msg: str = ""):
        """标记任务失败（自动递增 retry_count）。

        若 retry_count >= max_retries，状态设为 failed 不再重试；
        否则重置为 pending 等待下次轮询。
        """
        task.retry_count = (task.retry_count or 0) + 1
        task.error_msg = error_msg
        task.updated_at = datetime.now(timezone.utc)
        if task.retry_count >= (task.max_retries or 3):
            task.status = "failed"
        else:
            task.status = "pending"  # 回退到 pending，等待下次轮询
        session.flush()

    @staticmethod
    def delete_completed(session: Session, before_hours: int = 24):
        """清理超过指定时间的已完成/已失败任务。"""
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=before_hours)
        session.query(CompensationTask).filter(
            CompensationTask.status.in_(["success", "failed"]),
            CompensationTask.updated_at < cutoff,
        ).delete()
        session.flush()
