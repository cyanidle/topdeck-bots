from __future__ import annotations

import ast
import logging
import pathlib
import unittest

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "topbot"
LOGGER_NAMES = {"log", "logger"}


def reserved_keys() -> set[str]:
    return set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


def extra_keys(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name)
                and func.value.id in LOGGER_NAMES):
            continue
        for keyword in node.keywords:
            if keyword.arg != "extra" or not isinstance(keyword.value, ast.Dict):
                continue
            for key in keyword.value.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    found.append((node.lineno, key.value))
    return found


class LogExtraKeysTest(unittest.TestCase):
    def test_no_reserved_logrecord_keys_in_extra(self):
        reserved = reserved_keys()
        problems = [f"{path.relative_to(PACKAGE.parent)}:{lineno} extra key {key!r} is reserved by logging"
                    for path in sorted(PACKAGE.rglob("*.py"))
                    for lineno, key in extra_keys(ast.parse(path.read_text(encoding="utf-8")))
                    if key in reserved]
        self.assertEqual(problems, [], "\n" + "\n".join(problems))

    def test_detector_sees_literal_keys(self):
        tree = ast.parse('log.info("x", extra={"name": 1, "user_id": 2})')
        self.assertEqual([key for _, key in extra_keys(tree)], ["name", "user_id"])

    def test_reserved_keys_include_logrecord_attributes(self):
        reserved = reserved_keys()
        for key in ("name", "module", "msg", "args", "message", "filename"):
            self.assertIn(key, reserved)


if __name__ == "__main__":
    unittest.main()
