"""数据库 CRUD 操作封装（按实体拆分为子模块）。

用法:
    from database.operations import DocOps, ModuleOps, BindingOps, GlossaryOps

    with get_session() as session:
        doc = DocOps.get_document(session, doc_id)
        session.commit()
"""

from database.operations.docs import DocOps
from database.operations.modules import ModuleOps
from database.operations.bindings import BindingOps
from database.operations.glossary import GlossaryOps
from database.operations.analysis import AnalysisOps
from database.operations.compensation import CompensationOps

__all__ = ["DocOps", "ModuleOps", "BindingOps", "GlossaryOps", "AnalysisOps",
           "CompensationOps"]
