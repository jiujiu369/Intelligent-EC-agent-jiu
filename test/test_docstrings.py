"""检查项目函数文档字符串的完整性与统一格式。"""

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {".git", ".venv", "venv", "__pycache__", "build", "dist"}


def _python_files():
    """收集项目内需要检查的 Python 源文件。
    :return: 返回函数处理得到的结果。
    """
    return [
        path
        for path in PROJECT_ROOT.rglob("*.py")
        if not EXCLUDED_PARTS.intersection(path.relative_to(PROJECT_ROOT).parts)
    ]


def _function_arguments(node):
    """提取函数声明中需要记录说明的参数名称。
    :param node: 需要检查的 AST 函数定义节点。
    :return: 返回函数处理得到的结果。
    """
    arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
    names = [argument.arg for argument in arguments if argument.arg not in {"self", "cls"}]
    if node.args.vararg:
        names.append(node.args.vararg.arg)
    if node.args.kwarg:
        names.append(node.args.kwarg.arg)
    return names


def _returns_value(node):
    """判断函数自身是否包含返回有效值的语句。
    :param node: 需要检查的 AST 函数定义节点。
    :return: 返回函数处理得到的结果。
    """
    class ReturnVisitor(ast.NodeVisitor):
        """只检查目标函数本身，不进入其内部定义的函数。

        :param node: AST 子节点。
        :return: 无返回值；检查结果记录在 ``found`` 属性中。
        """

        def __init__(self):
            """初始化返回值检查状态。"""
            self.found = False

        def visit_Return(self, return_node):
            """记录返回非空值的语句。
            :param return_node: AST 返回语句节点。
            """
            if return_node.value is not None:
                self.found = True

        def visit_FunctionDef(self, function_node):
            """阻止检查进入嵌套的同步函数。
            :param function_node: 传入 ``function_node`` 的业务数据。
            """

        def visit_AsyncFunctionDef(self, function_node):
            """阻止检查进入嵌套的异步函数。
            :param function_node: 传入 ``function_node`` 的业务数据。
            """

        def visit_Lambda(self, lambda_node):
            """阻止检查进入匿名函数。
            :param lambda_node: 匿名函数的 AST 节点。
            """

    visitor = ReturnVisitor()
    for statement in node.body:
        visitor.visit(statement)
    return visitor.found


def test_all_functions_have_structured_docstrings():
    """确保项目全部函数使用包含参数与返回值说明的统一文档格式。"""
    problems = []
    for path in _python_files():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            docstring = ast.get_docstring(node, clean=False)
            location = f"{path.relative_to(PROJECT_ROOT)}:{node.lineno} {node.name}"
            if not docstring:
                problems.append(f"{location} 缺少文档字符串")
                continue
            for argument_name in _function_arguments(node):
                if f":param {argument_name}:" not in docstring:
                    problems.append(f"{location} 缺少 :param {argument_name}:")
            if _returns_value(node) and ":return:" not in docstring:
                problems.append(f"{location} 缺少 :return:")

    assert not problems, "\n" + "\n".join(problems)
