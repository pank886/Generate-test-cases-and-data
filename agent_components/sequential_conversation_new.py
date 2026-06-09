import json
import os
from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, TypedDict, List, Dict, Any

import httpx
from dotenv import load_dotenv, find_dotenv
from langchain_core.prompts import ChatPromptTemplate

# LangChain 相关
from langchain_openai import ChatOpenAI
from langchain_classic.memory import ConversationSummaryBufferMemory

from agent_components.chromadb_file import ReadersChromadb
from prompts.response_model import ProperResponse, ApiDefinition, TestCase, TestData, AssertionRule, ExecutionResult, TestReport
from prompts.definitions import PromptFactory

load_dotenv(find_dotenv())


# ---  定义 State  ---
class State(TypedDict):
    user_input: str
    original_input: str  # 专门用来存第一次的输入
    context: str
    chat_history: list

    response_obj: "ProperResponse"

    api_definition_list: Optional[List[ApiDefinition]]
    test_case: Optional[TestCase]
    test_data: Optional[TestData]
    assertion: Optional[AssertionRule]
    current_step_index: int  # 当前执行索引（游标）
    context_variables: Dict[str, Any]  # 全局记忆库：{ "login": {"token": "xyz"}, "order": {"id": 123} }
    execution_result: Optional[ExecutionResult]  # 当前步骤的执行结果


class ApiDefinitionList(BaseModel):
    """包装类：用于让 LLM 输出接口列表"""
    apis: List[ApiDefinition] = Field(..., description="提取到的所有接口定义列表")

class ChatTestAgentGraph:
    def __init__(self, db_path: Optional[str] = None):
        self.llm = ChatOpenAI(
            model=os.environ.get("LLM_MODEL"),
            base_url=os.environ.get("LANGCHAIN_URL"),
            api_key=os.environ.get("LLM_API_KEY"),
            temperature=0.7,
            tiktoken_model_name="gpt-3.5-turbo"
        )

        self.memory = ConversationSummaryBufferMemory(
            llm=self.llm,
            max_token_limit=10000,
            return_messages=True,
            memory_key="chat_history",
            input_key="user_input"
        )

        self.prompt_factory = PromptFactory()

        self.vector_store = None
        if db_path:
            self.vector_store = ReadersChromadb(persist_directory=db_path)

        # 提示词和 Chain
        prompt_template = self.prompt_factory.get_prompt_template()
        self.chain = prompt_template | self.llm.with_structured_output(ProperResponse, strict=False)

    def _retrieve_node(self, state: State):
        """检索知识库 (包装器)"""
        print("🔍 [节点] 正在调用外部工具检索...")

        #检查是否有知识库
        if not self.vector_store:
            context = "未检索到知识库"
        else:
            context = self.vector_store.search_context(user_question_str=state["user_input"])
        return {"context": context}

    def _generate_node(self, state: State):
        """生成回复"""
        print("🧠 [节点] 正在生成回复...")

        # 加载记忆
        memory_vars = self.memory.load_memory_variables({"user_input": state["user_input"]})
        history = memory_vars["chat_history"]

        # 调用 Chain
        response = self.chain.invoke({
            "context": state["context"],
            "chat_history": history,
            "user_input": state["user_input"]
        })

        return {"response_obj": response}

    def _save_memory_node(self, state: State):
        """保存完整测试报告到文件,直接序列化 State"""
        print("💾 [节点] 正在持久化完整测试数据...")

        # 1. 准备保存的数据目录
        save_dir = "test_history"
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)

        # 2. 将 Pydantic 模型转换为字典 (处理 datetime 等特殊类型)
        def serialize(obj):
            if hasattr(obj, 'model_dump'): # Pydantic V2
                return obj.model_dump()
            return str(obj)

        # 3. 提取关键数据
        save_data = {
            "timestamp": datetime.now().isoformat(),
            "user_input": state.get("user_input"),
            "api_definition_list": serialize(state.get("api_definition_list")),
            "test_case": serialize(state.get("test_case")),
            "test_data": serialize(state.get("test_data")),
            "assertion": serialize(state.get("assertion")),
            "execution_result": serialize(state.get("execution_result"))
        }

        # 4. 写入 JSON 文件
        filename = f"{save_dir}/test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(save_data, f, indent=2, ensure_ascii=False)
            print(f"   ✅ 数据已保存至: {filename}")
        except Exception as e:
            print(f"   ❌ 保存失败: {e}")
        return {}

    def _keep_memories_alive_node(self, state: State):
        """多轮对话保存记忆"""
        print("💾 [节点] 正在保存记忆...")
        self.memory.save_context(
            {"user_input": state["user_input"]},
            {"output": state["response_obj"].final_response}
        )
        return {}

    def _parse_api_node(self, state: State):
        """分析接口定义"""
        print("\n正在分析文档，提取接口定义...")

        prompt = self.prompt_factory.parse_api_node()
        chain = prompt | self.llm.with_structured_output(ApiDefinitionList, strict=False)

        result = chain.invoke({"content": state["context"],
                               "user_context": state["original_input"]
                               })

        api_list = result.apis

        if isinstance(api_list, list):
            print(f"   🛠️ 成功提取到 {len(api_list)} 个接口:")
            for api in api_list:
                print(f"      - {api.name}: {api.url}")
        else:
            # 理论上不会走到这里，除非 LLM 没遵守指令
            print(f"   ⚠️ 提取结果异常: {result}")
            api_list = []

        return {"api_definition_list": api_list}

    def _generate_casse_node(self, state: State):
        """生成测试用例"""
        print("\n📝 正在设计测试用例...")

        prompt = self.prompt_factory.generate_case_node()
        chain = prompt | self.llm.with_structured_output(TestCase, strict=True)
        all_apis_dict = [api.model_dump() for api in state["api_definition_list"]]
        all_apis_json = json.dumps(all_apis_dict, indent=2, ensure_ascii=False)

        result = chain.invoke({
            "all_apis_info": all_apis_json,
            "user_context": state["original_input"]
        })

        print(f"   🧪 用例: {result.title}")
        return {"test_case": result}

    def _generate_data_node(self, state: State):
        """生成测试数据"""
        print("\n🔢 正在构造测试数据...")

        prompt = self.prompt_factory.generate_data_node()
        chain = prompt | self.llm.with_structured_output(TestData, strict=True)

        all_apis_dict = [api.model_dump() for api in state["api_definition_list"]]
        all_apis_json = json.dumps(all_apis_dict, indent=2, ensure_ascii=False)

        case_obj = state.get("test_case")

        result = chain.invoke({
            "all_apis_info": all_apis_json,
            "user_context": state["original_input"],
            "test_case_logic": case_obj.pre_condition if case_obj else "无特定逻辑"
        })

        # 打印预览
        print(f"   📦 共生成 {len(result.steps)} 个步骤的数据:")
        for i, step in enumerate(result.steps):
            print(f"      步骤 {i + 1}: {step.method} {step.url}")

        return {"test_data": result}

    def _get_default_assertion(self):
        """获取默认断言"""
        return AssertionRule(field="code", operator="equals", expected_value=200)

    def _generate_assertion_node(self, state: State):
        """生成断言规则"""
        print("\n⚖️ 正在制定断言规则...")

        test_data = state.get("test_data")
        if not test_data:
            print("   ⚠️ 警告：State 中未找到测试数据 (test_data)，跳过 LLM 调用，使用默认断言。")
            return {"assertion": self._get_default_assertion()}

        test_case = state.get("test_case")
        if not test_case:
            print("   ⚠️ 警告：State 中未找到测试用例 (test_case)，跳过 LLM 调用，使用默认断言。")
            return {"assertion": self._get_default_assertion()}

        # 2. 准备 Prompt
        prompt = self.prompt_factory.generate_assertion_node()
        tools = [AssertionRule]
        llm_with_tools = self.llm.bind_tools(tools)
        chain = prompt | llm_with_tools

        # 3. 构造数据
        case_desc = test_case.description
        data_payload = json.dumps(test_data.payload, ensure_ascii=False)

        # 4. 调用
        response = chain.invoke({
            "test_case_desc": case_desc,
            "test_data_payload": data_payload
        })

        # 5. 提取结果
        if response.tool_calls:
            tool_call = response.tool_calls[0]
            if tool_call['name'] == 'AssertionRule':
                result = AssertionRule(**tool_call['args'])
            else:
                result = self._get_default_assertion()
        else:
            print("⚠️ 模型未调用工具，使用默认断言")
            result = self._get_default_assertion()

        print(f"   🎯 断言: {result.field} {result.operator} {result.expected_value}")
        return {"assertion": result}


    def _execute_test_node(self, state: State):
        """发送 HTTP 请求"""

        # 1. 获取当前状态
        current_index = state.get("current_step_index", 0)
        test_data_obj = state.get("test_data")

        # 边界检查
        if not test_data_obj or current_index >= len(test_data_obj.steps):
            return {"current_step_index": current_index}

        # 2. 直接从 test_data 获取当前步骤的完整数据
        current_step = test_data_obj.steps[current_index]

        print(f"\n🚀 [{current_index + 1}/{len(test_data_obj.steps)}] 执行: {current_step.method} {current_step.url}")

        # 3. 发送请求 (直接使用 step 里的数据)
        try:
            with httpx.Client() as client:
                response = client.request(
                    method=current_step.method,
                    url=current_step.url,
                    json=current_step.payload,   # 直接用生成的 payload
                    headers=current_step.headers, # 直接用生成的 headers
                    timeout=5.0
                )
                status_code = response.status_code
                response_body = response.text

        except Exception as e:
            status_code = 0
            response_body = str(e)
            print(f"   ❌ 请求异常: {e}")

        # 4. 返回结果
        # 注意：这里不再更新 context_variables，也不再动态提取参数
        return {
            "current_step_index": current_index + 1, # 游标后移，触发下一轮
            "execution_result": ExecutionResult(
                step_name=f"Step {current_index + 1}", # 简单命名为 Step 1, Step 2...
                status_code=status_code,
                response_body=response_body,
                is_success=(status_code == 200),
                error_message=None
            )
        }

    def _generate_report_node(self, state: State):
        """生成报告"""
        print("\n📊 生成最终报告...")

        test_case = state.get("test_case")
        execution_result = state.get("execution_result")

        case_json = test_case.model_dump_json(indent=2) if test_case else "无测试用例信息"
        result_json = execution_result.model_dump_json(indent=2) if execution_result else "无执行结果信息"

        prompt = self.prompt_factory.generate_report_node()
        chain = prompt | self.llm.with_structured_output(TestReport)
        result = chain.invoke({"test_case_info": case_json, "execution_result": result_json})

        print(f"✅ 报告生成完毕: {result.test_title} - {'成功' if result.test_result else '失败'}")
        return print("暂时丢弃，后序专门放个文件存储")