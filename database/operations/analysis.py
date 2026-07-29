"""module_analysis 表 CRUD 操作。"""

import json as _json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from database.models import ModuleAnalysis


class AnalysisOps:
    """模块场景分析记录的 CRUD 操作集合（全部 @staticmethod，session 为第一参数）。"""

    @staticmethod
    def get_by_module_id(session: Session, module_id: str) -> ModuleAnalysis | None:
        """按 module_id（UUID）查询分析记录。"""
        return session.query(ModuleAnalysis).filter_by(module_id=module_id).first()

    @staticmethod
    def upsert(session: Session, module_id: str, module_name: str,
               analysis_json: str) -> ModuleAnalysis:
        """创建或覆盖分析记录（同一 module_id 只有一条）。

        已存在 → 覆盖 analysis_json、更新 module_name、version++、重置 status 为 draft。
        不存在 → 创建新记录。
        """
        existing = session.query(ModuleAnalysis).filter_by(module_id=module_id).first()
        if existing:
            existing.module_name = module_name
            existing.analysis_json = analysis_json
            existing.status = "draft"
            existing.version = (existing.version or 0) + 1
            existing.modified_at = datetime.now(timezone.utc)
            session.flush()
            return existing
        else:
            record = ModuleAnalysis(
                module_id=module_id,
                module_name=module_name,
                analysis_json=analysis_json,
                status="draft",
                version=1,
            )
            session.add(record)
            session.flush()
            return record

    @staticmethod
    def delete_by_module_id(session: Session, module_id: str):
        """删除模块的分析记录（无记录时静默成功）。"""
        session.query(ModuleAnalysis).filter_by(module_id=module_id).delete()
        session.flush()
