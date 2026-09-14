#!/usr/bin/env python
"""Unit tests for AST symbol extraction, workspace indexing, and codebase intelligence."""
import json
import os
import shutil
import tempfile
import time
import unittest

from agent.symbols import (
    SymbolInfo,
    WorkspaceSymbolIndex,
    parse_python_symbols,
    parse_regex_symbols,
)
from agent.tools import build_tools


class TestASTSymbolExtraction(unittest.TestCase):
    def test_parse_python_symbols_comprehensive(self):
        code = '''"""Module docstring."""
import os

class Animal:
    """Base animal class."""
    species: str = "unknown"

    def __init__(self, name: str, age: int = 0) -> None:
        """Initialize animal."""
        self.name = name
        self.age = age

    async def speak(self, volume: float = 1.0) -> str:
        return "..."

class Dog(Animal):
    def bark(self, count: int = 1) -> str:
        return "woof" * count

def create_pet(name: str) -> Dog:
    """Factory function for pets."""
    return Dog(name)
'''
        symbols = parse_python_symbols("pets.py", code)
        self.assertEqual(len(symbols), 6)

        # Animal class
        animal = symbols[0]
        self.assertEqual(animal.name, "Animal")
        self.assertEqual(animal.kind, "class")
        self.assertEqual(animal.line, 4)
        self.assertEqual(animal.docstring, "Base animal class.")

        # Animal.__init__
        init_m = symbols[1]
        self.assertEqual(init_m.name, "__init__")
        self.assertEqual(init_m.kind, "method")
        self.assertEqual(init_m.parent, "Animal")
        self.assertIn("self", init_m.signature)
        self.assertIn("name: str", init_m.signature)
        self.assertIn("age: int=0", init_m.signature)

        # Animal.speak (async)
        speak_m = symbols[2]
        self.assertEqual(speak_m.name, "speak")
        self.assertEqual(speak_m.parent, "Animal")
        self.assertIn("volume: float=1.0", speak_m.signature)

        # Dog class
        dog = symbols[3]
        self.assertEqual(dog.name, "Dog")
        self.assertEqual(dog.kind, "class")
        self.assertEqual(dog.signature, "(Animal)")

        # Dog.bark
        bark_m = symbols[4]
        self.assertEqual(bark_m.name, "bark")
        self.assertEqual(bark_m.parent, "Dog")

        # create_pet top-level function
        factory = symbols[5]
        self.assertEqual(factory.name, "create_pet")
        self.assertEqual(factory.kind, "function")
        self.assertEqual(factory.parent, None)
        self.assertEqual(factory.docstring, "Factory function for pets.")

    def test_parse_regex_symbols_javascript(self):
        js_code = """
export class UserService extends BaseService {
  constructor() {}
}

export async function fetchUser(id) {
  return null;
}

const computeHash = (data) => {
  return "hash";
};
"""
        symbols = parse_regex_symbols("user.ts", js_code, "typescript")
        names = [s.name for s in symbols]
        self.assertIn("UserService", names)
        self.assertIn("fetchUser", names)
        self.assertIn("computeHash", names)

    def test_parse_regex_symbols_golang(self):
        go_code = """
package main

func (s *Server) Start(port int) error {
    return nil
}

func Helper(val string) bool {
    return true
}
"""
        symbols = parse_regex_symbols("main.go", go_code, "go")
        self.assertEqual(len(symbols), 2)
        start_m = [s for s in symbols if s.name == "Start"][0]
        self.assertEqual(start_m.kind, "method")
        self.assertEqual(start_m.parent, "Server")

        helper_f = [s for s in symbols if s.name == "Helper"][0]
        self.assertEqual(helper_f.kind, "function")
        self.assertIsNone(helper_f.parent)


class TestWorkspaceSymbolIndex(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="test_symbols_")
        # Create a mini project layout
        os.makedirs(os.path.join(self.root, "pkg"), exist_ok=True)
        with open(os.path.join(self.root, "pkg", "calc.py"), "w", encoding="utf-8") as f:
            f.write("class Calculator:\n    def add(self, a, b):\n        return a + b\n\ndef compute():\n    c = Calculator()\n    return c.add(1, 2)\n")
        with open(os.path.join(self.root, "main.py"), "w", encoding="utf-8") as f:
            f.write("from pkg.calc import Calculator, compute\n\nif __name__ == '__main__':\n    print(compute())\n")
        with open(os.path.join(self.root, "pytest.ini"), "w", encoding="utf-8") as f:
            f.write("[pytest]\n")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_get_outline(self):
        index = WorkspaceSymbolIndex(self.root)
        outline = index.get_outline("pkg/calc.py")
        self.assertIn("pkg/calc.py", outline)
        self.assertIn("class Calculator", outline)
        self.assertIn("def add", outline)
        self.assertIn("def compute", outline)

    def test_find_definition(self):
        index = WorkspaceSymbolIndex(self.root)
        def_res = index.find_definition("Calculator")
        self.assertIn("Found 1 definition", def_res)
        self.assertIn("class Calculator", def_res)
        self.assertIn("pkg/calc.py", def_res)

        method_res = index.find_definition("add")
        self.assertIn("method add(self, a, b)", method_res)
        self.assertIn("in Calculator", method_res)

    def test_find_references(self):
        index = WorkspaceSymbolIndex(self.root)
        ref_res = index.find_references("Calculator")
        self.assertIn("pkg/calc.py:1 [DEF]", ref_res)
        self.assertIn("main.py:1", ref_res)

    def test_get_workspace_summary(self):
        index = WorkspaceSymbolIndex(self.root)
        summary = index.get_workspace_summary()
        self.assertIn("Test runner: pytest", summary)
        self.assertIn("Entry points: main.py", summary)

    def test_cache_persistence_and_invalidation(self):
        index1 = WorkspaceSymbolIndex(self.root)
        cache_path = os.path.join(self.root, ".sova", "symbols_cache.json")
        self.assertTrue(os.path.exists(cache_path))

        with open(cache_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("pkg/calc.py", data["files"])

        # Modify file and verify incremental update
        time.sleep(0.05)
        with open(os.path.join(self.root, "pkg", "calc.py"), "a", encoding="utf-8") as f:
            f.write("\ndef multiply(a, b):\n    return a * b\n")

        index2 = WorkspaceSymbolIndex(self.root)
        def_res = index2.find_definition("multiply")
        self.assertIn("Found 1 definition", def_res)
        self.assertIn("function multiply(a, b)", def_res)


class TestToolsIntegration(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="test_tools_")
        with open(os.path.join(self.root, "app.py"), "w", encoding="utf-8") as f:
            f.write("def run():\n    pass\n")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_tools_contain_symbol_functions(self):
        schemas, impls = build_tools(self.root)
        tool_names = [s["function"]["name"] for s in schemas]
        self.assertIn("get_outline", tool_names)
        self.assertIn("find_definition", tool_names)
        self.assertIn("find_references", tool_names)
        self.assertIn("workspace_summary", tool_names)

        outline = impls["get_outline"]("app.py")
        self.assertIn("def run", outline)

        definition = impls["find_definition"]("run")
        self.assertIn("app.py", definition)

        summary = impls["workspace_summary"]()
        self.assertIn("Entry points: app.py", summary)


if __name__ == "__main__":
    unittest.main()
