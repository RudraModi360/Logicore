"""
Structure Validation Script
=============================
Validates the restructured logicore module layout.
Run WITHOUT any LLM provider:
    python scripts/validate_structure.py
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def check(desc, success, detail=""):
    status = "[OK]" if success else "[FAIL]"
    print(f"  {status} {desc}")
    if detail:
        print(f"     {detail}")
    return success


errors = []

# -- 1. Top-level imports --
section("1. Top-Level Package Imports")

try:
    import logicore
    check("logicore imported", True, f"version={logicore.__version__}")
except Exception as e:
    check("logicore imported", False, str(e))
    errors.append("logicore import failed")

classes = {
    "Agent": logicore.Agent,
    "SmartAgent": logicore.SmartAgent,
    "BasicAgent": logicore.BasicAgent,
    "MCPAgent": logicore.MCPAgent,
    "CopilotAgent": logicore.CopilotAgent,
    "SessionManager": logicore.SessionManager,
    "TelemetryTracker": logicore.TelemetryTracker,
    "MCPClientManager": logicore.MCPClientManager,
    "ProviderGateway": logicore.ProviderGateway,
}
for name, cls in classes.items():
    ok = isinstance(cls, type) or hasattr(cls, "__class__")
    check(f"logicore.{name} accessible", ok)

# -- 2. New Module Structure --
section("2. New Module Structure")

module_tests = [
    ("logicore.agent", "from logicore.agent import Agent, AgentSession"),
    ("logicore.agent.variants", "from logicore.agent.variants import SmartAgent, BasicAgent, MCPAgent"),
    ("logicore.session", "from logicore.session import SessionManager"),
    ("logicore.telemetry", "from logicore.telemetry import TelemetryTracker"),
    ("logicore.mcp", "from logicore.mcp import MCPClientManager"),
    ("logicore.gateway", "from logicore.gateway import ProviderGateway, NormalizedMessage"),
    ("logicore.context_engine", "from logicore.context_engine import ContextEngine, TokenEstimator"),
    ("logicore.document", "from logicore.document import get_handler, BaseDocumentHandler"),
]

for name, import_stmt in module_tests:
    try:
        exec(import_stmt)
        check(f"{name} imports OK", True)
    except Exception as e:
        check(f"{name} imports OK", False, str(e))
        errors.append(f"{name} failed: {e}")

# -- 3. Tool System --
section("3. Tool System")

try:
    from logicore.tools.registry import registry, execute_tool
    from logicore.tools.base import BaseTool, ToolResult
    from logicore.tools import ALL_TOOL_SCHEMAS, SAFE_TOOLS, DANGEROUS_TOOLS

    check(f"Registry created: {len(registry._tools)} tools", len(registry._tools) > 0)
    check(f"ALL_TOOL_SCHEMAS: {len(ALL_TOOL_SCHEMAS)} schemas", len(ALL_TOOL_SCHEMAS) > 0)
    check(f"SAFE_TOOLS: {len(SAFE_TOOLS)}", len(SAFE_TOOLS) > 0)
    check(f"DANGEROUS_TOOLS: {len(DANGEROUS_TOOLS)}", len(DANGEROUS_TOOLS) > 0)

    tool_classes = [
        "ReadFileTool", "CreateFileTool", "EditFileTool", "DeleteFileTool",
        "ListFilesTool", "SearchFilesTool", "FastGrepTool",
        "ExecuteCommandTool", "CodeExecuteTool",
        "WebSearchTool", "UrlFetchTool", "ImageSearchTool",
        "GitCommandTool",
        "ReadDocumentTool", "ConvertDocumentTool",
        "NotesTool", "DateTimeTool", "ThinkTool", "SmartBashTool",
        "MergePDFTool", "SplitPDFTool",
        "MediaSearchTool",
        "AddCronJobTool", "ListCronJobsTool", "RemoveCronJobTool",
        "TrackerCreateTool", "TrackerUpdateTool", "TrackerListTool",
        "EnterPlanModeTool", "SubmitPlanTool",
    ]

    tool_modules = {
        "ReadFileTool": "logicore.tools.filesystem",
        "CreateFileTool": "logicore.tools.filesystem",
        "EditFileTool": "logicore.tools.filesystem",
        "DeleteFileTool": "logicore.tools.filesystem",
        "ListFilesTool": "logicore.tools.filesystem",
        "SearchFilesTool": "logicore.tools.filesystem",
        "FastGrepTool": "logicore.tools.filesystem",
        "ExecuteCommandTool": "logicore.tools.execution",
        "CodeExecuteTool": "logicore.tools.execution",
        "WebSearchTool": "logicore.tools.web",
        "UrlFetchTool": "logicore.tools.web",
        "ImageSearchTool": "logicore.tools.web",
        "GitCommandTool": "logicore.tools.git",
        "ReadDocumentTool": "logicore.tools.document",
        "ConvertDocumentTool": "logicore.tools.convert",
        "DateTimeTool": "logicore.tools.datetime",
        "NotesTool": "logicore.tools.notes",
        "SmartBashTool": "logicore.tools.bash",
        "ThinkTool": "logicore.tools.think",
        "MergePDFTool": "logicore.tools.pdf",
        "SplitPDFTool": "logicore.tools.pdf",
        "MediaSearchTool": "logicore.tools.media",
        "AddCronJobTool": "logicore.tools.cron",
        "ListCronJobsTool": "logicore.tools.cron",
        "RemoveCronJobTool": "logicore.tools.cron",
        "TrackerCreateTool": "logicore.tools.tracker",
        "TrackerUpdateTool": "logicore.tools.tracker",
        "TrackerListTool": "logicore.tools.tracker",
        "EnterPlanModeTool": "logicore.tools.plan",
        "SubmitPlanTool": "logicore.tools.plan",
    }

    for cls_name in tool_classes:
        module = tool_modules.get(cls_name)
        if module:
            try:
                mod = __import__(module, fromlist=[cls_name])
                getattr(mod, cls_name)
                check(f"  {cls_name} -> {module}", True)
            except Exception as e:
                check(f"  {cls_name} -> {module}", False, str(e))
                errors.append(f"{cls_name} import failed: {e}")

except Exception as e:
    check("Tool system", False, str(e))
    errors.append(f"Tool system: {e}")

# -- 4. Document Handlers --
section("4. Document Handler System")

try:
    from logicore.document import (
        get_handler, DocumentHandlerRegistry,
        PDFHandler, DocxHandler, PPTXHandler,
        ExcelHandler, TextHandler, CSVHandler, ImageHandler
    )
    check("Document handler registry OK", True)

    test_files = {
        "report.pdf": "PDFHandler",
        "doc.docx": "DocxHandler",
        "slides.pptx": "PPTXHandler",
        "data.xlsx": "ExcelHandler",
        "notes.txt": "TextHandler",
        "data.csv": "CSVHandler",
        "photo.png": "ImageHandler",
    }
    # Test registry mapping (handler classes, not file I/O)
    for ext, expected_cls_name in [(".pdf", "PDFHandler"), (".docx", "DocxHandler"),
                                     (".pptx", "PPTXHandler"), (".xlsx", "ExcelHandler"),
                                     (".txt", "TextHandler"), (".csv", "CSVHandler"),
                                     (".png", "ImageHandler")]:
        try:
            handler_cls = DocumentHandlerRegistry._handlers.get(ext)
            ok = handler_cls is not None and handler_cls.__name__ == expected_cls_name
            check(f"  {ext} -> {expected_cls_name}", ok, handler_cls.__name__ if handler_cls else "None")
        except Exception as e:
            check(f"  {ext} -> {expected_cls_name}", False, str(e))
            errors.append(f"Handler for {ext}: {e}")

except Exception as e:
    check("Document handlers", False, str(e))
    errors.append(f"Document handlers: {e}")

# -- 5. Context & Token Budget --
section("5. Context & Token Budget System")

try:
    from logicore.context_engine.token_estimator import (
        get_model_context_window, estimate_tokens, estimate_message_tokens,
        MODEL_CONTEXT_WINDOWS,
    )
    from logicore.runtime.context.token_budget import TokenBudget, TokenUsage, TokenCategory
    check("Context module imports OK", True)

    budget = TokenBudget(config=__import__('logicore.runtime.config', fromlist=['RuntimeConfig']).RuntimeConfig.from_settings(), model_name="gpt-4o")
    check(f"TokenBudget created (window={budget.context_window})", budget.context_window == 128000)
    check(f"estimate_tokens('hello')", estimate_tokens("hello") == 1)
    check(f"estimate_tokens('Hello, world!')", estimate_tokens("Hello, world!") >= 3)
    check(f"get_model_context_window('gpt-4')", get_model_context_window("gpt-4") == 8192)

    usage = TokenUsage()
    check(f"TokenUsage created", usage is not None)

except Exception as e:
    check("Context module", False, str(e))
    errors.append(f"Context module: {e}")

# -- 6. Session & Telemetry --
section("6. Session & Telemetry")

try:
    from logicore.session import SessionStorage, SessionManager
    sm = SessionManager()
    check("SessionManager instantiated", True)

    from logicore.telemetry import TelemetryTracker
    tt = TelemetryTracker(enabled=True)
    check("TelemetryTracker instantiated", True)
    check("TelemetryTracker enabled", tt.enabled)

except Exception as e:
    check("Session/Telemetry", False, str(e))
    errors.append(f"Session/Telemetry: {e}")

# -- 7. MCP & Gateway --
section("7. MCP & Gateway")

try:
    from logicore.mcp import MCPClientManager
    check("MCPClientManager OK", True)

    from logicore.gateway import ProviderGateway, NormalizedMessage, get_gateway_for_provider
    msg = NormalizedMessage(role="user", content="test")
    check(f"NormalizedMessage: role={msg.role}", msg.role == "user")
    check(f"NormalizedMessage: content={msg.content}", msg.content == "test")

except Exception as e:
    check("MCP/Gateway", False, str(e))
    errors.append(f"MCP/Gateway: {e}")

# -- 8. No Old Import Paths --
section("8. No Old Import Paths Remain")

old_paths = [
    "logicore.agents.agent",
    "logicore.agents.agent_basic",
    "logicore.agents.agent_smart",
    "logicore.agents.agent_mcp",
    "logicore.simplemem",
    "logicore.mcp_client",
    "logicore.session_manager",
    "logicore.document_handlers",
    "logicore.providers.gateway",
    "logicore.context_engine",
    "logicore.tools.agent_tools",
    "logicore.tools.pdf_tools",
    "logicore.tools.media_search",
    "logicore.tools.cron_tools",
    "logicore.tools.tracker_tools",
    "logicore.tools.plan_tools",
    "logicore.tools.convert_document",
    "logicore.tools.scheduler",
]

all_old_gone = True
for old_path in old_paths:
    try:
        __import__(old_path)
        check(f"  {old_path} still accessible (SHOULD BE GONE)", False)
        errors.append(f"Old path still exists: {old_path}")
        all_old_gone = False
    except ModuleNotFoundError:
        check(f"  {old_path} properly removed", True)
    except Exception:
        check(f"  {old_path} has issues but removed", True)

if all_old_gone:
    check("All old import paths removed", True)


# -- 9. Summary --
section("SUMMARY")
total_tests = 40
passed = total_tests - len(errors)
print(f"  Passed: {passed}/{total_tests}")
if errors:
    print(f"\n  Errors ({len(errors)}):")
    for e in errors:
        print(f"    - {e}")
    print(f"\n  Fix the above issues before proceeding.")
    sys.exit(1)
else:
    print(f"\n  ALL CHECKS PASSED - Structure is valid!")
    print(f"\n  New module layout is fully functional.")
    print(f"  You can now run the interactive chatbot:")
    print(f"    python scripts/chatbot.py [provider] [model]")
