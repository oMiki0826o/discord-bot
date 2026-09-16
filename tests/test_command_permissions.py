import ast
from pathlib import Path


def test_default_permissions_always_have_runtime_permission_check() -> None:
    """Discord 側的預設權限不能單獨當作 Bot 的授權邊界。"""
    cogs_dir = Path(__file__).parents[1] / "cogs"
    missing: list[str] = []

    for path in sorted(cogs_dir.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            decorators = [ast.unparse(decorator) for decorator in node.decorator_list]
            has_default = any("app_commands.default_permissions" in item for item in decorators)
            has_runtime = any("app_commands.checks.has_permissions" in item for item in decorators)
            if has_default and not has_runtime:
                missing.append(f"{path.relative_to(cogs_dir)}:{node.lineno} ({node.name})")

    assert missing == [], "敏感指令缺少執行期權限檢查: " + ", ".join(missing)
