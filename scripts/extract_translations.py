"""Update JSON message catalogs without overwriting existing translations.

German source phrases are stable message identifiers. Dynamic texts use named
placeholders. This extractor also includes worker diagnostics for local display.
"""

import ast
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def human(value):
    if not value or not any(c.isalpha() for c in value):
        return False
    if value.startswith(("http", "/api/", "library/", "custom_img/", "<")):
        return False
    return bool(re.search(r"[äöüÄÖÜß]|\b[A-Z][a-z]+(?:\s|:|$)|\b[a-z]+ [a-z]+", value))


def template(node):
    result, count = "", 0
    for part in node.values:
        if isinstance(part, ast.Constant):
            result += part.value.replace("{", "{{").replace("}", "}}")
        else:
            key = "p" + str(count)
            conversion = "!" + chr(part.conversion) if part.conversion != -1 else ""
            spec = ""
            if part.format_spec and all(
                isinstance(x, ast.Constant) for x in part.format_spec.values
            ):
                spec = ":" + "".join(x.value for x in part.format_spec.values)
            result += "{" + key + conversion + spec + "}"
            count += 1
    return result


def messages():
    result = set()
    for path in (ROOT / "src" / "teddystudio").glob("*.py"):
        if path.name in {"i18n.py", "i18n_widgets.py", "selftest.py"}:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if path.stem.startswith("ui_") or path.name == "app.py":
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id in {"tr_text", "_f"}
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                ):
                    result.add(node.args[0].value)
        else:
            parents = {
                child: node
                for node in ast.walk(tree)
                for child in ast.iter_child_nodes(node)
            }
            for node in ast.walk(tree):
                if isinstance(node, ast.JoinedStr):
                    value = template(node)
                    if human(value):
                        result.add(value)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if isinstance(parents.get(node), (ast.JoinedStr, ast.Expr)):
                        continue
                    if human(node.value):
                        result.add(node.value)
    return result


def main():
    import argparse
    import sys

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="Validate catalogs without modifying files"
    )
    options = parser.parse_args()
    sys.path.insert(0, str(ROOT / "src"))
    from teddystudio.i18n import validate_catalog

    keys = messages()
    folder = ROOT / "src" / "teddystudio" / "locales"
    for path in folder.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        previous = data.get("messages", {})
        if options.check:
            validate_catalog(data)
            missing = keys - previous.keys()
            if missing:
                raise SystemExit(
                    f"{path.name}: {len(missing)} missing message identifiers"
                )
            continue
        data["messages"] = {
            key: key if data["code"] == "de" else previous.get(key, key)
            for key in sorted(keys)
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    print(f"Extracted {len(keys)} message identifiers")


if __name__ == "__main__":
    main()
