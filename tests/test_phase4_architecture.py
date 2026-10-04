"""Phase 4 public errors and executable runtime boundaries."""

import ast
from pathlib import Path
import re
import tomllib

import anyio
from mcp.shared.memory import create_connected_server_and_client_session
import pytest

from cognivault.contracts import GatewayError
from cognivault.transports.mcp_stdio import create_mcp_server


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "src" / "cognivault"

# Model/inference namespace words catch provider families and generic SDK names.
# Deliberately omit broad networking packages such as httpx and requests.
MODEL_NAMESPACE_WORDS = {
    "aiplatform", "anthropic", "bedrock", "cohere", "deepseek", "fireworks",
    "genai", "generativeai", "groq", "huggingface", "inference", "langchain",
    "litellm", "llama", "llm", "mistral", "mistralai", "modelscope", "ollama", "openai",
    "replicate", "tensorflow", "together", "torch", "transformers", "vertexai",
    "vllm",
}
MODEL_CLIENT_NAMES = {
    "OpenAI", "AsyncOpenAI", "Anthropic", "AsyncAnthropic", "CohereClient",
    "GenerativeModel", "ChatOpenAI", "ChatAnthropic",
}


def _prohibited_module(name: str) -> bool:
    words = set(re.split(r"[._-]+", name.lower()))
    return bool(words & MODEL_NAMESPACE_WORDS) or ("model" in words and bool(words & {"sdk", "client", "api"}))


def _model_client_name(name: str) -> bool:
    return name in MODEL_CLIENT_NAMES or bool(
        re.fullmatch(r"(?:Async)?(?:Inference|Generative|LanguageModel|ChatModel|Completion|Embedding|LLM|Model)\w*(?:Client|Model)", name)
    )


def _module_violations(source: str) -> list[str]:
    """Inspect executable syntax; comments and documentation do not count."""
    tree = ast.parse(source)
    violations = []
    importlib_aliases = set()
    importer_aliases = {"__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _prohibited_module(alias.name):
                    violations.append(f"import {alias.name}")
                if alias.name == "importlib":
                    importlib_aliases.add(alias.asname or "importlib")
        elif isinstance(node, ast.ImportFrom):
            if node.module and _prohibited_module(node.module):
                violations.append(f"from {node.module}")
            for alias in node.names:
                if node.module and _prohibited_module(f"{node.module}.{alias.name}") and not _prohibited_module(node.module):
                    violations.append(f"from {node.module} import {alias.name}")
                elif _model_client_name(alias.name) and not (node.module and _prohibited_module(node.module)):
                    violations.append(f"from {node.module} import {alias.name}")
            if node.module == "importlib":
                importer_aliases.update(
                    alias.asname or alias.name for alias in node.names if alias.name == "import_module"
                )
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and _model_client_name(func.id):
                violations.append(f"client {func.id}")
            dynamic_import = (
                isinstance(func, ast.Name) and func.id in importer_aliases
                or isinstance(func, ast.Attribute) and func.attr == "import_module"
                and isinstance(func.value, ast.Name) and func.value.id in importlib_aliases
            )
            if dynamic_import and node.args and isinstance(node.args[0], ast.Constant):
                module = node.args[0].value
                if isinstance(module, str) and _prohibited_module(module):
                    violations.append(f"dynamic import {module}")
    return violations


def _package_name(requirement: str) -> str:
    name = re.match(r"^\s*([A-Za-z0-9_.-]+)", requirement)
    assert name is not None, f"Invalid project dependency: {requirement!r}"
    return re.sub(r"[-_.]+", "-", name.group(1)).lower()


def _runtime_dependency_violations(project: dict) -> list[str]:
    return [
        f"pyproject.toml: {dependency}"
        for dependency in project.get("dependencies", []) if _prohibited_module(_package_name(dependency))
    ]


def _core_transport_violations(source: str) -> list[str]:
    violations = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
            if (node.level > 0 or node.module == "cognivault") and any(
                alias.name in {"transports", "mcp"} for alias in node.names
            ):
                violations.append("transport import from package root")
        else:
            continue
        if any(
            name == "mcp" or name.startswith("mcp.")
            or name == "transports" or name.startswith("transports.")
            or name == "cognivault.transports"
            or name.startswith("cognivault.transports.")
            for name in names
        ):
            violations.extend(names)
    return violations


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import openai", "import openai"),
        ("from anthropic import Anthropic", "from anthropic"),
        ("from google import genai", "from google import genai"),
        ("from huggingface_hub import InferenceClient", "from huggingface_hub"),
        ("from google.cloud import aiplatform", "from google.cloud import aiplatform"),
        ("from acme_sdk import InferenceClient", "from acme_sdk import InferenceClient"),
        ("import acme_inference_sdk", "import acme_inference_sdk"),
        ("import importlib as il\nil.import_module('google.genai')", "dynamic import google.genai"),
        ("from importlib import import_module as load\nload('ollama')", "dynamic import ollama"),
        ("__import__('transformers')", "dynamic import transformers"),
        ("client = OpenAI()", "client OpenAI"),
    ],
)
def test_runtime_scanner_detects_model_clients_and_imports(source: str, expected: str) -> None:
    assert expected in _module_violations(source)


def test_runtime_scanner_ignores_non_executable_mentions() -> None:
    assert _module_violations('"""openai client"""\n# import anthropic\nname = "OpenAI"') == []


def test_runtime_scanner_allows_general_http_clients() -> None:
    assert _module_violations("import httpx\nfrom urllib import request\nimport requests") == []


def test_runtime_scanner_allows_domain_model_modules_without_client_sdk() -> None:
    assert _module_violations("from .model import Lesson\nimport cognivault.model") == []


def test_runtime_dependency_policy_rejects_inference_sdk_names() -> None:
    project = {"dependencies": ["huggingface-hub>=0.20", "google-cloud-aiplatform>=1", "acme-model-sdk>=2"]}
    assert _runtime_dependency_violations(project) == [
        "pyproject.toml: huggingface-hub>=0.20",
        "pyproject.toml: google-cloud-aiplatform>=1",
        "pyproject.toml: acme-model-sdk>=2",
    ]


def test_runtime_dependency_policy_excludes_optional_dev_packages() -> None:
    project = {"dependencies": ["mcp>=1"], "optional-dependencies": {"dev": ["openai>=1"]}}
    assert _runtime_dependency_violations(project) == []


@pytest.mark.parametrize(
    "source",
    [
        "from . import transports",
        "from .. import transports",
        "from cognivault import transports",
    ],
)
def test_core_import_scan_detects_package_root_transport_imports(source: str) -> None:
    assert _core_transport_violations(source)


def test_runtime_modules_and_project_dependencies_have_no_model_clients() -> None:
    violations = [
        f"{path.relative_to(ROOT)}: {violation}"
        for path in RUNTIME.rglob("*.py")
        for violation in _module_violations(path.read_text(encoding="utf-8"))
    ]
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    violations.extend(_runtime_dependency_violations(project))
    assert violations == []


def test_core_modules_do_not_import_the_mcp_transport() -> None:
    core = [RUNTIME / name for name in ("config.py", "contracts.py", "gateway.py", "policy.py", "runtime.py")]
    core += list((RUNTIME / "adapters").rglob("*.py"))
    violations = []
    for path in core:
        violations.extend(
            f"{path.relative_to(ROOT)}: {violation}"
            for violation in _core_transport_violations(path.read_text(encoding="utf-8"))
        )
    assert violations == []


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("QMD_RUNTIME_UNSAFE", "Study runtime is unsafe"),
        ("HISTORY_UNAVAILABLE", "History is unavailable"),
        ("RESOURCE_NOT_FOUND", "Resource was not found"),
        ("CONFLICT", "Request conflicts with existing data"),
        ("PERMISSION_DENIED", "Operation is not permitted"),
        ("PAYLOAD_TOO_LARGE", "Request exceeds the size limit"),
        ("STORAGE_UNAVAILABLE", "Local storage is unavailable"),
        ("UNSUPPORTED_MEDIA_TYPE", "Media type is unsupported"),
    ],
)
def test_public_domain_errors_are_safe_at_mcp_boundary(code: str, message: str) -> None:
    class FailingGateway:
        def health_report(self):
            raise GatewayError(code, r"C:\private\secret")

    async def check():
        async with create_connected_server_and_client_session(create_mcp_server(FailingGateway())) as client:
            response = await client.call_tool("health_report", {})
            assert response.isError is True
            assert response.structuredContent == {"ok": False, "error": {"code": code, "message": message}}
            assert "C:\\private" not in str(response.content)

    anyio.run(check)
