from pathlib import Path
import ast
import re

ROOT = Path(__file__).resolve().parents[1]
BLENDER = ROOT / "blender" / "blendgimp"
paths = list(BLENDER.rglob("*.py"))
texts = {p: p.read_text(encoding="utf-8") for p in paths}
all_text = "\n".join(texts.values())

problems = []

for path, text in texts.items():
    tree = ast.parse(text, filename=str(path))
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    imports = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                imports[item.asname or item.name.split(".")[0]] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for item in node.names:
                if item.name != "*":
                    imports[item.asname or item.name] = node.lineno
    for name, line in imports.items():
        if name not in used:
            problems.append(f"unused import {path.relative_to(ROOT)}:{line}: {name}")

    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name not in {"register", "unregister"}:
            count = len(re.findall(rf"\b{re.escape(node.name)}\b", all_text))
            if count == 1:
                problems.append(f"unreferenced top-level function {path.relative_to(ROOT)}:{node.lineno}: {node.name}")
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if not isinstance(target, ast.Name) or target.id.startswith("__"):
                    continue
                count = len(re.findall(rf"\b{re.escape(target.id)}\b", all_text))
                if count == 1:
                    problems.append(f"unreferenced top-level constant {path.relative_to(ROOT)}:{node.lineno}: {target.id}")

if problems:
    print("Source hygiene FAIL")
    for problem in problems:
        print(" -", problem)
    raise SystemExit(1)

print(f"BlendGimp 0.5.21 source hygiene: PASS ({len(paths)} Blender Python files)")
