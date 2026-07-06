"""Phase C 硬编码模拟数据（A 阶段上线后替换为真实检索结果）。

数据结构：
  MOCK_PRODUCT_DOCS: {模块名: [{module, content, related_modules}]}
  MOCK_API_DEFS:      {模块名: [{name, url, method, params, returns}]}
"""

MOCK_PRODUCT_DOCS = {
    "合同管理": [
        {
            "module": "合同管理",
            "content": "合同管理模块是整个系统的核心模块，负责合同的创建、审批、签署、归档全生命周期管理。主要功能包括合同起草、合同审批流程、电子签章、合同变更、合同终止。",
            "related_modules": ["房产模块", "商户模块"],
        },
        {
            "module": "合同管理",
            "content": "合同签约场景：用户选择已录入的房产和商户信息，填写合同条款（租金、周期、付款方式），上传附件，提交审批。审批通过后进入电子签章环节。",
            "related_modules": ["房产模块", "商户模块"],
        },
        {
            "module": "合同管理",
            "content": "合同变更是对已生效合同进行条款修改，需重新走审批流程。变更记录需完整留痕，支持版本追溯。",
            "related_modules": [],
        },
    ],
    "房产模块": [
        {
            "module": "房产模块",
            "content": "房产模块负责管理所有物业资产信息，包括房产基本信息（地址、面积、户型）、产权信息、房产状态（空置/出租/维修）。合同签约时需要选择房产作为标的物。",
            "related_modules": ["合同管理"],
        },
    ],
    "商户模块": [
        {
            "module": "商户模块",
            "content": "商户模块管理所有合作商户信息，包括商户基本信息（名称、法人、联系方式）、资质文件、信用评级。合同签约时需要选择商户作为签约方。",
            "related_modules": ["合同管理"],
        },
    ],
}

MOCK_API_DEFS = {
    "合同管理": [
        {
            "name": "合同创建", "url": "/api/contract/create", "method": "POST",
            "params": {"house_id": "string", "merchant_id": "string", "terms": "object"},
            "returns": {"contract_id": "string", "status": "string"},
        },
        {
            "name": "合同审批", "url": "/api/contract/approve", "method": "POST",
            "params": {"contract_id": "string", "action": "string"},
            "returns": {"success": "boolean", "code": "integer"},
        },
        {
            "name": "合同查询", "url": "/api/contract/list", "method": "GET",
            "params": {"page": "integer", "size": "integer", "status": "string"},
            "returns": {"total": "integer", "list": "array"},
        },
    ],
    "房产模块": [
        {
            "name": "房产信息查询", "url": "/api/house/info", "method": "GET",
            "params": {"house_id": "string"},
            "returns": {"house_id": "string", "address": "string", "area": "number", "status": "string"},
        },
    ],
    "商户模块": [
        {
            "name": "商户信息查询", "url": "/api/merchant/info", "method": "GET",
            "params": {"merchant_id": "string"},
            "returns": {"merchant_id": "string", "name": "string", "credit_rating": "string"},
        },
    ],
    "公共基础服务": [
        {
            "name": "人员查询", "url": "/api/user/search", "method": "GET",
            "params": {"keyword": "string"},
            "returns": {"user_list": "array"},
        },
        {
            "name": "文件上传", "url": "/api/file/upload", "method": "POST",
            "params": {"file": "binary"},
            "returns": {"file_id": "string"},
        },
    ],
}
