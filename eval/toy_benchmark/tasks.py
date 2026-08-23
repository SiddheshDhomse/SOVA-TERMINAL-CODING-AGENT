"""Tiny handmade tasks + shell checks for learning eval mechanics without Docker."""

TASKS = [
    {
        "id": "fix_add",
        "files": {
            "calc.py": "def add(a, b):\n    return a - b\n",
        },
        "task": (
            "The add function in calc.py has a bug: it subtracts instead of adding. "
            "Fix it so add(a, b) returns a + b."
        ),
        "check_cmd": (
            "python -c \"import calc; assert calc.add(2, 3) == 5, 'add is still broken'; print('OK')\""
        ),
    },
    {
        "id": "reverse_string",
        "files": {
            "strings_util.py": "",
        },
        "task": (
            "Implement a function reverse_string(s) in strings_util.py that returns "
            "the input string reversed."
        ),
        "check_cmd": (
            "python -c \"import strings_util; "
            "assert strings_util.reverse_string('abc') == 'cba', 'reverse_string incorrect'; "
            "print('OK')\""
        ),
    },
]
