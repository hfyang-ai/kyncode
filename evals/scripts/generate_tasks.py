"""数据驱动生成 50 道 KynCode 测评任务（幂等，可重复执行）。

产物：
- tasks/<task_id>.yaml         任务定义（prompt + 判分配置）
- datasets/<category>/<task_id>/setup/  初始代码
- datasets/<category>/<task_id>/tests/  隐藏测试（test_f2p_*.py / test_p2p_*.py）

运行：python scripts/generate_tasks.py
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = ROOT / "tasks"
DATASETS_DIR = ROOT / "datasets"


def _pkg(n_modules: int, bug: int | None = None) -> dict[str, str]:
    """生成含 n_modules 个模块的包，用于长上下文任务。

    每个模块 mod_XX.py 定义一个 func_XX(x) = x * XX。
    bug 指定某个模块编号，该模块的乘数写成 999（明显错误），
    用于让 agent 在多个文件里定位这一处错误。
    """
    files: dict[str, str] = {"pkg/__init__.py": ""}
    for i in range(n_modules):
        mul = 999 if bug == i else i
        files[f"pkg/mod_{i:02d}.py"] = f"def func_{i}(x):\n    return x * {mul}\n"
    return files


def _long_file(n_funcs: int, bug: int | None = None) -> str:
    """生成长文件源码：n_funcs 个函数，bug 指定某个函数用错误乘数 999。"""
    body: list[str] = []
    for i in range(n_funcs):
        mul = 999 if bug == i else i
        body.append(f"def func_{i}(x):\n    return x * {mul}\n")
    return "\n".join(body)


# 每个任务：setup/tests 是 {文件名: 内容} 映射；answer_match 类 tests 为空。
TASKS: list[dict[str, Any]] = [
    # =====================================================================
    # basic —— 文件读取（answer_match，8 题）
    # =====================================================================
    {
        "id": "basic_001",
        "category": "basic",
        "kind": "answer_match",
        "title": "读取配置文件字段",
        "prompt": (
            "当前目录有一个 config.json。读取它，回答其中 `port` 字段的值。\n"
            "只输出一个整数，不要任何解释。"
        ),
        "setup": {
            "config.json": '{"app_name": "demo", "port": 8080, "debug": false, "db_host": "localhost"}\n',
        },
        "tests": {},
        "grader": {"type": "answer_match", "answer_pattern": "8080"},
    },
    {
        "id": "basic_002",
        "category": "basic",
        "kind": "answer_match",
        "title": "统计多文件总行数",
        "prompt": (
            "data/ 目录下有 a.txt、b.txt、c.txt 三个文件。统计它们的总行数（空行也算一行）。\n"
            "只输出一个整数。"
        ),
        "setup": {
            "data/a.txt": "alpha\nbeta\n",
            "data/b.txt": "one\ntwo\nthree\n",
            "data/c.txt": "single\n",
        },
        "tests": {},
        "grader": {"type": "answer_match", "answer_pattern": r"\b6\b"},
    },
    {
        "id": "basic_003",
        "category": "basic",
        "kind": "answer_match",
        "title": "从 README 找运行命令",
        "prompt": (
            "阅读 README.md，找出「运行测试」对应的命令。\n"
            "只输出该命令本身，不要引号、不要解释。"
        ),
        "setup": {
            "README.md": (
                "# Demo\n\n## Install\n```\npip install -e .\n```\n\n"
                "## Run tests\n```\npytest -q\n```\n\n## Run\n```\npython main.py\n```\n"
            ),
        },
        "tests": {},
        "grader": {"type": "answer_match", "answer_pattern": r"pytest"},
    },
    {
        "id": "basic_004",
        "category": "basic",
        "kind": "answer_match",
        "title": "从日志定位错误码",
        "prompt": (
            "app.log 里有若干行日志。找出第一条包含 ERROR 的行，回答它的错误码（形如 E123）。\n"
            "只输出错误码。"
        ),
        "setup": {
            "app.log": (
                "2026-10-26 10:00:00 INFO server started\n"
                "2026-10-26 10:00:01 INFO request /health\n"
                "2026-10-26 10:00:02 ERROR failed to connect E503\n"
                "2026-10-26 10:00:03 INFO retry\n"
                "2026-10-26 10:00:04 ERROR timeout E504\n"
            ),
        },
        "tests": {},
        "grader": {"type": "answer_match", "answer_pattern": "E503"},
    },
    {
        "id": "basic_005",
        "category": "basic",
        "kind": "answer_match",
        "title": "读取深层嵌套文件",
        "prompt": (
            "在 src/pkg/deep/ 下有一个 settings.yaml。读取它，回答其中 `region` 的值。\n"
            "只输出 region 的值。"
        ),
        "setup": {
            "src/pkg/deep/settings.yaml": "region: ap-southeast-1\nreplicas: 3\n",
            "src/pkg/__init__.py": "",
        },
        "tests": {},
        "grader": {"type": "answer_match", "answer_pattern": "ap-southeast-1"},
    },
    {
        "id": "basic_006",
        "category": "basic",
        "kind": "answer_match",
        "title": "理解函数行为",
        "prompt": (
            "阅读 math_utils.py。调用 multiply(3, 4) 会返回什么？\n"
            "只输出一个整数。"
        ),
        "setup": {
            "math_utils.py": (
                "def add(a, b):\n    return a + b\n\n\n"
                "def subtract(a, b):\n    return a - b\n\n\n"
                "def multiply(a, b):\n    return a * b\n"
            ),
        },
        "tests": {},
        "grader": {"type": "answer_match", "answer_pattern": r"\b12\b"},
    },
    {
        "id": "basic_007",
        "category": "basic",
        "kind": "answer_match",
        "title": "从 CSV 提取统计",
        "prompt": (
            "data.csv 的列是 name,age。找出 age 列的最大值。\n"
            "只输出一个整数。"
        ),
        "setup": {
            "data.csv": "name,age\nalice,30\nbob,25\ncarol,42\ndave,37\n",
        },
        "tests": {},
        "grader": {"type": "answer_match", "answer_pattern": r"\b42\b"},
    },
    {
        "id": "basic_008",
        "category": "basic",
        "kind": "answer_match",
        "title": "总结模块职责",
        "prompt": (
            "阅读 handlers.py，用一句话说明 send_email 函数的作用。\n"
            "回答必须包含「邮件」和「发送」两个词。"
        ),
        "setup": {
            "handlers.py": (
                "import smtplib\n\n\n"
                "def send_email(to, subject, body):\n"
                "    # connect to SMTP and dispatch an email message\n"
                "    smtp = smtplib.SMTP('localhost')\n"
                "    smtp.sendmail('noreply@x.com', to, f'{subject}\\n{body}')\n"
            ),
        },
        "tests": {},
        "grader": {"type": "answer_match", "answer_pattern": r"(?=.*邮件)(?=.*发送)"},
    },
    # =====================================================================
    # basic —— 跨文件修改（code_change，7 题）
    # =====================================================================
    {
        "id": "basic_009",
        "category": "basic",
        "kind": "code_change",
        "title": "重命名函数并更新调用点",
        "prompt": (
            "calc.py 里定义了 foo 函数，main.py 调用了它。\n"
            "把 foo 重命名为 add，并同步更新所有调用它的地方。不要改变函数行为。"
        ),
        "setup": {
            "calc.py": "def foo(a, b):\n    return a + b\n",
            "main.py": "from calc import foo\n\n\ndef run():\n    return foo(1, 2)\n",
        },
        "tests": {
            "test_f2p_rename.py": (
                "from calc import add\n\n\n"
                "def test_add_renamed():\n"
                "    assert add(1, 2) == 3\n\n\n"
                "def test_foo_gone():\n"
                "    import calc\n"
                "    assert not hasattr(calc, 'foo')\n"
            ),
            "test_p2p_callers.py": (
                "import main\n\n\n"
                "def test_main_still_works():\n"
                "    assert main.run() == 3\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "basic_010",
        "category": "basic",
        "kind": "code_change",
        "title": "新增函数并在别处调用",
        "prompt": (
            "在 text.py 里新增一个 farewell(name) 函数，返回 'Goodbye, {name}!'。\n"
            "然后在 app.py 里调用它打印 'Goodbye, World!'。"
        ),
        "setup": {
            "text.py": "def greet(name):\n    return f'Hello, {name}!'\n",
            "app.py": "from text import greet\n\nprint(greet('World'))\n",
        },
        "tests": {
            "test_f2p_farewell.py": (
                "from text import farewell\n\n\n"
                "def test_farewell():\n"
                "    assert farewell('World') == 'Goodbye, World!'\n"
            ),
            "test_p2p_greet.py": (
                "from text import greet\n\n\n"
                "def test_greet_unchanged():\n"
                "    assert greet('World') == 'Hello, World!'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "basic_011",
        "category": "basic",
        "kind": "code_change",
        "title": "消除硬编码改用配置常量",
        "prompt": (
            "config.py 里定义了 PORT = 8000。server.py 里硬编码了 port = 8080。\n"
            "修改 server.py，让它使用 config.py 的 PORT 常量。"
        ),
        "setup": {
            "config.py": "PORT = 8000\nHOST = '0.0.0.0'\n",
            "server.py": "port = 8080  # hardcoded, should use config.PORT\n\n\ndef get_port():\n    return port\n",
        },
        "tests": {
            "test_f2p_config.py": (
                "import server\nimport config\n\n\n"
                "def test_port_from_config():\n"
                "    assert server.get_port() == config.PORT\n\n\n"
                "def test_port_value():\n"
                "    assert server.get_port() == 8000\n"
            ),
            "test_p2p_config_constant.py": (
                "import config\n\n\n"
                "def test_config_unchanged():\n"
                "    assert config.PORT == 8000\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "basic_012",
        "category": "basic",
        "kind": "code_change",
        "title": "抽取重复代码到公共模块",
        "prompt": (
            "a.py 和 b.py 里都有一个 normalize 函数，实现完全相同（strip + 转小写）。\n"
            "把它抽取到新建的 utils.py，并让 a.py、b.py 从 utils 导入使用。"
        ),
        "setup": {
            "a.py": (
                "def normalize(s):\n    return s.strip().lower()\n\n\n"
                "def use_a():\n    return normalize('  AbC ')\n"
            ),
            "b.py": (
                "def normalize(s):\n    return s.strip().lower()\n\n\n"
                "def use_b():\n    return normalize('  XyZ ')\n"
            ),
        },
        "tests": {
            "test_f2p_utils.py": (
                "import utils\n\n\n"
                "def test_utils_has_normalize():\n"
                "    assert utils.normalize('  Hi ') == 'hi'\n"
            ),
            "test_p2p_behavior.py": (
                "import a, b\n\n\n"
                "def test_a():\n"
                "    assert a.use_a() == 'abc'\n\n\n"
                "def test_b():\n"
                "    assert b.use_b() == 'xyz'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "basic_013",
        "category": "basic",
        "kind": "code_change",
        "title": "修改接口签名并更新调用方",
        "prompt": (
            "api.py 的 get_user(user_id) 现在需要增加一个 include_avatar 参数（默认 True）。\n"
            "更新 api.py 的签名，并同步更新 caller.py 里的调用，使其显式传 include_avatar=False。"
        ),
        "setup": {
            "api.py": (
                "def get_user(user_id):\n"
                "    return {'id': user_id, 'name': 'Alice', 'avatar': 'a.png'}\n"
            ),
            "caller.py": (
                "from api import get_user\n\n\n"
                "def show():\n    u = get_user(1)\n    return u['name']\n"
            ),
        },
        "tests": {
            "test_f2p_signature.py": (
                "import inspect\nfrom api import get_user\n\n\n"
                "def test_has_include_avatar():\n"
                "    sig = inspect.signature(get_user)\n"
                "    assert 'include_avatar' in sig.parameters\n\n\n"
                "def test_no_avatar():\n"
                "    u = get_user(1, include_avatar=False)\n"
                "    assert u['name'] == 'Alice'\n"
            ),
            "test_p2p_caller.py": (
                "import caller\n\n\n"
                "def test_caller():\n"
                "    assert caller.show() == 'Alice'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "basic_014",
        "category": "basic",
        "kind": "code_change",
        "title": "移动常量到独立模块",
        "prompt": (
            "logic.py 里硬编码了 MAX_RETRY = 3。把它移到新建的 constants.py，\n"
            "并让 logic.py 从 constants 导入使用。"
        ),
        "setup": {
            "logic.py": (
                "MAX_RETRY = 3\n\n\n"
                "def attempts():\n    return MAX_RETRY\n"
            ),
        },
        "tests": {
            "test_f2p_constants.py": (
                "import constants\nimport logic\n\n\n"
                "def test_constant_moved():\n"
                "    assert constants.MAX_RETRY == 3\n\n\n"
                "def test_logic_uses_constant():\n"
                "    assert logic.attempts() == constants.MAX_RETRY\n"
            ),
            "test_p2p_value.py": (
                "import logic\n\n\n"
                "def test_attempts():\n"
                "    assert logic.attempts() == 3\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "basic_015",
        "category": "basic",
        "kind": "code_change",
        "title": "补全类型注解且不改行为",
        "prompt": (
            "funcs.py 里的函数都没有类型注解。给每个函数的参数和返回值补上类型注解。\n"
            "不要改变任何函数的行为。"
        ),
        "setup": {
            "funcs.py": (
                "def concat(a, b):\n    return a + b\n\n\n"
                "def is_positive(n):\n    return n > 0\n"
            ),
        },
        "tests": {
            "test_f2p_annotations.py": (
                "import inspect\nimport funcs\n\n\n"
                "def test_concat_annotated():\n"
                "    sig = inspect.signature(funcs.concat)\n"
                "    assert sig.return_annotation is not inspect.Signature.empty\n\n\n"
                "def test_is_positive_annotated():\n"
                "    sig = inspect.signature(funcs.is_positive)\n"
                "    assert sig.return_annotation is not inspect.Signature.empty\n"
            ),
            "test_p2p_behavior.py": (
                "import funcs\n\n\n"
                "def test_concat():\n"
                "    assert funcs.concat('a', 'b') == 'ab'\n\n\n"
                "def test_is_positive():\n"
                "    assert funcs.is_positive(5) is True\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    # =====================================================================
    # bugfix —— Bug 修复（code_change，10 题）
    # =====================================================================
    {
        "id": "bugfix_001",
        "category": "bugfix",
        "kind": "code_change",
        "title": "修复除零错误",
        "prompt": (
            "work.py 的 divide(a, b) 在 b 为 0 时会崩溃。\n"
            "请修复它，使得当除数为 0 时返回 0，其余情况正常相除。"
        ),
        "setup": {
            "work.py": "def divide(a, b):\n    return a / b\n",
        },
        "tests": {
            "test_f2p_zero.py": (
                "from work import divide\n\n\n"
                "def test_divide_by_zero():\n"
                "    assert divide(10, 0) == 0\n"
            ),
            "test_p2p_normal.py": (
                "from work import divide\n\n\n"
                "def test_divide_normal():\n"
                "    assert divide(10, 2) == 5\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "bugfix_002",
        "category": "bugfix",
        "kind": "code_change",
        "title": "修复 off-by-one",
        "prompt": (
            "work.py 的 last(items) 想返回列表最后一个元素，但现在会抛 IndexError。\n"
            "请修复索引错误，使其正确返回最后一个元素。"
        ),
        "setup": {
            "work.py": "def last(items):\n    return items[len(items)]\n",
        },
        "tests": {
            "test_f2p_last.py": (
                "from work import last\n\n\n"
                "def test_last():\n"
                "    assert last([1, 2, 3]) == 3\n"
            ),
            "test_p2p_single.py": (
                "from work import last\n\n\n"
                "def test_last_single():\n"
                "    assert last(['a']) == 'a'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "bugfix_003",
        "category": "bugfix",
        "kind": "code_change",
        "title": "修复重复次数少一",
        "prompt": (
            "work.py 的 repeat(s, n) 应该把字符串 s 重复 n 次，但现在少重复了一次。\n"
            "请修复。"
        ),
        "setup": {
            "work.py": "def repeat(s, n):\n    return s * (n - 1)\n",
        },
        "tests": {
            "test_f2p_repeat.py": (
                "from work import repeat\n\n\n"
                "def test_repeat_three():\n"
                "    assert repeat('ab', 3) == 'ababab'\n"
            ),
            "test_p2p_repeat_one.py": (
                "from work import repeat\n\n\n"
                "def test_repeat_once():\n"
                "    assert repeat('x', 1) == 'x'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "bugfix_004",
        "category": "bugfix",
        "kind": "code_change",
        "title": "修复空列表边界",
        "prompt": (
            "work.py 的 first(items) 在 items 为空时会抛 IndexError。\n"
            "请修复，使得空列表时返回 None，其余情况返回第一个元素。"
        ),
        "setup": {
            "work.py": "def first(items):\n    return items[0]\n",
        },
        "tests": {
            "test_f2p_empty.py": (
                "from work import first\n\n\n"
                "def test_first_empty():\n"
                "    assert first([]) is None\n"
            ),
            "test_p2p_nonempty.py": (
                "from work import first\n\n\n"
                "def test_first_nonempty():\n"
                "    assert first([1, 2]) == 1\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "bugfix_005",
        "category": "bugfix",
        "kind": "code_change",
        "title": "修复字符串相加而非数值相加",
        "prompt": (
            "work.py 的 parse_and_add(a, b) 接收数字，但当传入字符串 '1'、'2' 时\n"
            "会做字符串拼接返回 '12'。请修复，使其把输入转成数值再相加。"
        ),
        "setup": {
            "work.py": "def parse_and_add(a, b):\n    return a + b\n",
        },
        "tests": {
            "test_f2p_str_input.py": (
                "from work import parse_and_add\n\n\n"
                "def test_add_strings_as_numbers():\n"
                "    assert parse_and_add('1', '2') == 3\n"
            ),
            "test_p2p_int_input.py": (
                "from work import parse_and_add\n\n\n"
                "def test_add_ints():\n"
                "    assert parse_and_add(1, 2) == 3\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "bugfix_006",
        "category": "bugfix",
        "kind": "code_change",
        "title": "修复边界比较符号",
        "prompt": (
            "work.py 的 is_adult(age) 判断是否成年，但当前 18 岁被判断为未成年。\n"
            "请修复比较符号，使 18 岁及以上算成年。"
        ),
        "setup": {
            "work.py": "def is_adult(age):\n    return age > 18\n",
        },
        "tests": {
            "test_f2p_boundary.py": (
                "from work import is_adult\n\n\n"
                "def test_is_adult_at_18():\n"
                "    assert is_adult(18) is True\n"
            ),
            "test_p2p_other.py": (
                "from work import is_adult\n\n\n"
                "def test_20_is_adult():\n"
                "    assert is_adult(20) is True\n\n\n"
                "def test_17_not_adult():\n"
                "    assert is_adult(17) is False\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "bugfix_007",
        "category": "bugfix",
        "kind": "code_change",
        "title": "修复排序方向",
        "prompt": (
            "work.py 的 sorted_desc(items) 应该返回降序结果，但现在返回了升序。\n"
            "请修复。"
        ),
        "setup": {
            "work.py": "def sorted_desc(items):\n    return sorted(items)\n",
        },
        "tests": {
            "test_f2p_desc.py": (
                "from work import sorted_desc\n\n\n"
                "def test_sorted_desc():\n"
                "    assert sorted_desc([1, 3, 2]) == [3, 2, 1]\n"
            ),
            "test_p2p_empty.py": (
                "from work import sorted_desc\n\n\n"
                "def test_sorted_empty():\n"
                "    assert sorted_desc([]) == []\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "bugfix_008",
        "category": "bugfix",
        "kind": "code_change",
        "title": "修复字典键硬编码",
        "prompt": (
            "work.py 的 get_value(d, key) 应该返回 d[key]，但现在错误地写死了键名 'key'。\n"
            "请修复，使其使用传入的 key 参数。"
        ),
        "setup": {
            "work.py": "def get_value(d, key):\n    return d['key']\n",
        },
        "tests": {
            "test_f2p_key.py": (
                "from work import get_value\n\n\n"
                "def test_get_by_param():\n"
                "    assert get_value({'x': 1}, 'x') == 1\n"
            ),
            "test_p2p_other_key.py": (
                "from work import get_value\n\n\n"
                "def test_get_other_key():\n"
                "    assert get_value({'a': 2}, 'a') == 2\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "bugfix_009",
        "category": "bugfix",
        "kind": "code_change",
        "title": "修复正则匹配错误",
        "prompt": (
            "work.py 的 extract_digits(s) 应该用正则提取字符串里的所有数字，\n"
            "但现在错误地匹配了字母。请修复正则。"
        ),
        "setup": {
            "work.py": (
                "import re\n\n\n"
                "def extract_digits(s):\n    return re.findall(r'[a-z]+', s)\n"
            ),
        },
        "tests": {
            "test_f2p_digits.py": (
                "from work import extract_digits\n\n\n"
                "def test_extract_digits():\n"
                "    assert extract_digits('abc123') == ['123']\n"
            ),
            "test_p2p_no_digits.py": (
                "from work import extract_digits\n\n\n"
                "def test_no_digits():\n"
                "    assert extract_digits('xyz') == []\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "bugfix_010",
        "category": "bugfix",
        "kind": "code_change",
        "title": "修复路径拼接",
        "prompt": (
            "work.py 的 config_path(dir) 用字符串 '+' 拼接路径，导致结尾斜杠时出现双斜杠。\n"
            "请改用 os.path.join 拼接。"
        ),
        "setup": {
            "work.py": "def config_path(dir):\n    return dir + '/config.yaml'\n",
        },
        "tests": {
            "test_f2p_trailing_slash.py": (
                "from work import config_path\n\n\n"
                "def test_trailing_slash():\n"
                "    assert config_path('/etc/') == '/etc/config.yaml'\n"
            ),
            "test_p2p_plain.py": (
                "from work import config_path\n\n\n"
                "def test_plain():\n"
                "    assert config_path('/etc') == '/etc/config.yaml'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    # =====================================================================
    # bugfix —— 测试失败处理（code_change，5 题）
    # =====================================================================
    {
        "id": "bugfix_011",
        "category": "bugfix",
        "kind": "code_change",
        "title": "根据失败测试修复逻辑",
        "prompt": (
            "运行 pytest 会发现 test_work.py 失败。阅读失败信息，修复 work.py 中的逻辑错误，\n"
            "使所有测试通过。"
        ),
        "setup": {
            "work.py": "def average(nums):\n    return sum(nums) / len(nums) + 1\n",
            "test_work.py": (
                "from work import average\n\n\n"
                "def test_average():\n"
                "    assert average([2, 4, 6]) == 4\n"
            ),
        },
        "tests": {
            "test_f2p_average.py": (
                "from work import average\n\n\n"
                "def test_average_fixed():\n"
                "    assert average([2, 4, 6]) == 4\n\n\n"
                "def test_average_other():\n"
                "    assert average([1, 2, 3]) == 2\n"
            ),
            "test_p2p_empty_guard.py": (
                "from work import average\n\n\n"
                "def test_average_single():\n"
                "    assert average([7]) == 7\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "bugfix_012",
        "category": "bugfix",
        "kind": "code_change",
        "title": "修复返回值类型",
        "prompt": (
            "运行 pytest 会发现 test_work.py 失败：函数返回了字符串而非整数。\n"
            "阅读失败信息，修复 work.py 的返回值类型。"
        ),
        "setup": {
            "work.py": "def square(n):\n    return str(n * n)\n",
            "test_work.py": (
                "from work import square\n\n\n"
                "def test_square_type():\n"
                "    assert square(3) == 9\n"
                "    assert isinstance(square(3), int)\n"
            ),
        },
        "tests": {
            "test_f2p_square.py": (
                "from work import square\n\n\n"
                "def test_square_int():\n"
                "    assert square(3) == 9\n\n\n"
                "def test_square_is_int():\n"
                "    assert isinstance(square(4), int)\n"
            ),
            "test_p2p_zero.py": (
                "from work import square\n\n\n"
                "def test_square_zero():\n"
                "    assert square(0) == 0\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "bugfix_013",
        "category": "bugfix",
        "kind": "code_change",
        "title": "修复异常处理缺失",
        "prompt": (
            "运行 pytest 会发现 test_work.py 失败：parse_int 遇到非法输入时应抛 ValueError，\n"
            "但现在静默返回了 0。阅读失败信息修复。"
        ),
        "setup": {
            "work.py": "def parse_int(s):\n    try:\n        return int(s)\n    except ValueError:\n        return 0\n",
            "test_work.py": (
                "import pytest\nfrom work import parse_int\n\n\n"
                "def test_parse_raises():\n"
                "    with pytest.raises(ValueError):\n"
                "        parse_int('abc')\n"
            ),
        },
        "tests": {
            "test_f2p_raises.py": (
                "import pytest\nfrom work import parse_int\n\n\n"
                "def test_parse_raises():\n"
                "    with pytest.raises(ValueError):\n"
                "        parse_int('abc')\n"
            ),
            "test_p2p_valid.py": (
                "from work import parse_int\n\n\n"
                "def test_parse_valid():\n"
                "    assert parse_int('42') == 42\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "bugfix_014",
        "category": "bugfix",
        "kind": "code_change",
        "title": "修复边界值处理",
        "prompt": (
            "运行 pytest 会发现 test_work.py 失败：clamp 在 value 超过上界 hi 时\n"
            "错误地返回了下界 lo。阅读失败信息修复边界处理。"
        ),
        "setup": {
            "work.py": (
                "def clamp(value, lo, hi):\n"
                "    if value < lo:\n"
                "        return lo\n"
                "    if value > hi:\n"
                "        return lo\n"
                "    return value\n"
            ),
            "test_work.py": (
                "from work import clamp\n\n\n"
                "def test_clamp_upper():\n"
                "    assert clamp(20, 0, 10) == 10\n"
            ),
        },
        "tests": {
            "test_f2p_clamp.py": (
                "from work import clamp\n\n\n"
                "def test_clamp_upper():\n"
                "    assert clamp(20, 0, 10) == 10\n\n\n"
                "def test_clamp_lower():\n"
                "    assert clamp(-5, 0, 10) == 0\n"
            ),
            "test_p2p_mid.py": (
                "from work import clamp\n\n\n"
                "def test_clamp_mid():\n"
                "    assert clamp(5, 0, 10) == 5\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "bugfix_015",
        "category": "bugfix",
        "kind": "code_change",
        "title": "修复浮点精度",
        "prompt": (
            "运行 pytest 会发现 test_work.py 失败：sum_to_n 使用浮点累加导致精度误差。\n"
            "阅读失败信息，改用不会丢失精度的方式计算。"
        ),
        "setup": {
            "work.py": "def sum_to_n(n):\n    total = 0.0\n    for i in range(1, n + 1):\n        total += 0.1\n    return total\n",
            "test_work.py": (
                "from work import sum_to_n\n\n\n"
                "def test_sum_to_n():\n"
                "    assert sum_to_n(10) == 1.0\n"
            ),
        },
        "tests": {
            "test_f2p_sum.py": (
                "from work import sum_to_n\n\n\n"
                "def test_sum_to_n():\n"
                "    assert sum_to_n(10) == 1.0\n"
            ),
            "test_p2p_zero.py": (
                "from work import sum_to_n\n\n\n"
                "def test_sum_zero():\n"
                "    assert sum_to_n(0) == 0.0\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    # =====================================================================
    # tool_error —— 工具错误恢复（5 题）
    # =====================================================================
    {
        "id": "tool_error_001",
        "category": "tool_error",
        "kind": "answer_match",
        "title": "文件不存在时查找正确文件",
        "prompt": (
            "读取 config.json 中 version 字段的值。如果 config.json 不存在，\n"
            "用 ls 或 Glob 列出当前目录，找到实际存在的配置文件再读。\n"
            "只输出 version 的值。"
        ),
        "setup": {"actual_config.json": '{"version": "2.1.0"}\n'},
        "tests": {},
        "grader": {"type": "answer_match", "answer_pattern": "2.1.0"},
    },
    {
        "id": "tool_error_002",
        "category": "tool_error",
        "kind": "code_change",
        "title": "从语法错误恢复",
        "prompt": (
            "运行 pytest 会报语法错误（SyntaxError）。阅读报错信息，修复 work.py 的语法错误，\n"
            "使测试通过。"
        ),
        "setup": {
            "work.py": "def add(a, b)\n    return a + b\n",
            "test_work.py": (
                "from work import add\n\n\n"
                "def test_add():\n"
                "    assert add(1, 2) == 3\n"
            ),
        },
        "tests": {
            "test_f2p_add.py": (
                "from work import add\n\n\n"
                "def test_add():\n"
                "    assert add(1, 2) == 3\n"
            ),
            "test_p2p_add2.py": (
                "from work import add\n\n\n"
                "def test_add_other():\n"
                "    assert add(4, 5) == 9\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "tool_error_003",
        "category": "tool_error",
        "kind": "code_change",
        "title": "从导入错误恢复",
        "prompt": (
            "运行 main.py 会报 ModuleNotFoundError：找不到模块 utls。\n"
            "实际模块文件叫 utils.py。修复 main.py 的导入错误。"
        ),
        "setup": {
            "utils.py": "def helper():\n    return 42\n",
            "main.py": "from utls import helper\n\n\ndef run():\n    return helper()\n",
        },
        "tests": {
            "test_f2p_import.py": (
                "import main\n\n\n"
                "def test_run():\n"
                "    assert main.run() == 42\n"
            ),
            "test_p2p_utils.py": (
                "from utils import helper\n\n\n"
                "def test_helper():\n"
                "    assert helper() == 42\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "tool_error_004",
        "category": "tool_error",
        "kind": "code_change",
        "title": "从属性错误恢复",
        "prompt": (
            "运行 main.py 会报 AttributeError：Calc 对象没有 subtract 方法。\n"
            "实际方法叫 add。修复 main.py 的调用错误。"
        ),
        "setup": {
            "calc.py": "class Calc:\n    def add(self, a, b):\n        return a + b\n",
            "main.py": (
                "from calc import Calc\n\n\n"
                "def run():\n"
                "    c = Calc()\n"
                "    return c.subtract(1, 2)\n"
            ),
        },
        "tests": {
            "test_f2p_call.py": (
                "import main\n\n\n"
                "def test_run():\n"
                "    assert main.run() == 3\n"
            ),
            "test_p2p_calc.py": (
                "from calc import Calc\n\n\n"
                "def test_add():\n"
                "    assert Calc().add(1, 2) == 3\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "tool_error_005",
        "category": "tool_error",
        "kind": "code_change",
        "title": "从键错误恢复",
        "prompt": (
            "运行 main.py 会报 KeyError：字典里没有 'age' 键。\n"
            "阅读报错信息，修复 main.py 使其不再崩溃并正确返回 name 的值。"
        ),
        "setup": {
            "config.py": "DATA = {'name': 'alice'}\n",
            "main.py": (
                "from config import DATA\n\n\n"
                "def run():\n"
                "    return DATA['age']\n"
            ),
        },
        "tests": {
            "test_f2p_key.py": (
                "import main\n\n\n"
                "def test_run():\n"
                "    assert main.run() == 'alice'\n"
            ),
            "test_p2p_config.py": (
                "from config import DATA\n\n\n"
                "def test_data():\n"
                "    assert DATA['name'] == 'alice'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    # =====================================================================
    # long_context —— 长上下文（10 题）
    # =====================================================================
    {
        "id": "long_context_001",
        "category": "long_context",
        "kind": "code_change",
        "title": "多文件包中定位错误函数",
        "prompt": (
            "pkg 包里有 12 个模块 mod_00 到 mod_11，每个定义一个 func_XX(x) = x * XX。\n"
            "其中有一个模块把乘数错写成了 999。找出并修复这个错误。"
        ),
        "setup": {**_pkg(12, bug=7)},
        "tests": {
            "test_f2p_locate.py": (
                "from pkg.mod_07 import func_7\n\n\n"
                "def test_func_7_fixed():\n"
                "    assert func_7(2) == 14\n"
            ),
            "test_p2p_others.py": (
                "from pkg.mod_03 import func_3\n"
                "from pkg.mod_11 import func_11\n\n\n"
                "def test_func_3():\n"
                "    assert func_3(2) == 6\n\n\n"
                "def test_func_11():\n"
                "    assert func_11(2) == 22\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "long_context_002",
        "category": "long_context",
        "kind": "answer_match",
        "title": "追踪跨模块调用链",
        "prompt": (
            "有 a.py、b.py、c.py、d.py 四个模块形成调用链 a -> b -> c -> d。\n"
            "func_d 被哪个模块直接调用？只输出模块名（不含 .py）。"
        ),
        "setup": {
            "a.py": "from b import func_b\n\n\ndef func_a():\n    return func_b()\n",
            "b.py": "from c import func_c\n\n\ndef func_b():\n    return func_c()\n",
            "c.py": "from d import func_d\n\n\ndef func_c():\n    return func_d()\n",
            "d.py": "def func_d():\n    return 'leaf'\n",
        },
        "tests": {},
        "grader": {"type": "answer_match", "answer_pattern": r"\bc\b"},
    },
    {
        "id": "long_context_003",
        "category": "long_context",
        "kind": "code_change",
        "title": "长文件中修复错误函数",
        "prompt": (
            "big.py 有 60 个 func_XX 函数（func_0 到 func_59），每个返回 x * XX。\n"
            "其中 func_37 的乘数错写成了 999。定位并修复它。"
        ),
        "setup": {"big.py": _long_file(60, bug=37)},
        "tests": {
            "test_f2p_fix.py": (
                "from big import func_37\n\n\n"
                "def test_func_37():\n"
                "    assert func_37(2) == 74\n"
            ),
            "test_p2p_others.py": (
                "from big import func_10\n"
                "from big import func_59\n\n\n"
                "def test_func_10():\n"
                "    assert func_10(2) == 20\n\n\n"
                "def test_func_59():\n"
                "    assert func_59(2) == 118\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "long_context_004",
        "category": "long_context",
        "kind": "answer_match",
        "title": "汇总多模块公开函数数",
        "prompt": (
            "pkg 包里有 10 个模块，每个模块定义一个 func_XX 函数。\n"
            "统计这个包里总共有多少个 func_ 开头的公开函数。只输出一个整数。"
        ),
        "setup": {**_pkg(10)},
        "tests": {},
        "grader": {"type": "answer_match", "answer_pattern": r"\b10\b"},
    },
    {
        "id": "long_context_005",
        "category": "long_context",
        "kind": "code_change",
        "title": "拆分大文件为多模块",
        "prompt": (
            "把 big.py 拆成两个模块：func_0 到 func_5 移到 a.py，func_6 到 func_11 移到 b.py。\n"
            "拆分后 a.py、b.py 应能被正常导入，原函数行为不变。"
        ),
        "setup": {"big.py": _long_file(12)},
        "tests": {
            "test_f2p_split.py": (
                "from a import func_0\n"
                "from b import func_11\n\n\n"
                "def test_a():\n"
                "    assert func_0(2) == 0\n\n\n"
                "def test_b():\n"
                "    assert func_11(2) == 22\n"
            ),
            "test_p2p_more.py": (
                "from a import func_5\n"
                "from b import func_6\n\n\n"
                "def test_func_5():\n"
                "    assert func_5(2) == 10\n\n\n"
                "def test_func_6():\n"
                "    assert func_6(2) == 12\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "long_context_006",
        "category": "long_context",
        "kind": "answer_match",
        "title": "长日志中定位错误码",
        "prompt": (
            "long.log 有 300 行日志。找出唯一一条 ERROR 行，回答它的错误码（形如 E701）。\n"
            "只输出错误码。"
        ),
        "setup": {
            "long.log": "".join(
                (
                    f"2026-10-26 12:{i // 60:02d}:{i % 60:02d} ERROR conn refused E701\n"
                    if i == 250
                    else f"2026-10-26 12:{i // 60:02d}:{i % 60:02d} INFO ok\n"
                )
                for i in range(300)
            ),
        },
        "tests": {},
        "grader": {"type": "answer_match", "answer_pattern": "E701"},
    },
    {
        "id": "long_context_007",
        "category": "long_context",
        "kind": "code_change",
        "title": "多处引用常量改名",
        "prompt": (
            "constants.py 定义了 API_KEY，且有 8 个 consumer_XX.py 文件从它导入。\n"
            "把常量改名为 TOKEN，并同步更新所有 consumer 文件的导入，使它们仍能正常运行。"
        ),
        "setup": {
            "constants.py": "API_KEY = 'sk-old'\n",
            **{
                f"consumer_{i:02d}.py": "from constants import API_KEY\n\nVAL = API_KEY\n"
                for i in range(8)
            },
        },
        "tests": {
            "test_f2p_rename.py": (
                "import constants\n\n\n"
                "def test_token_exists():\n"
                "    assert constants.TOKEN == 'sk-old'\n\n\n"
                "def test_api_key_gone():\n"
                "    assert not hasattr(constants, 'API_KEY')\n"
            ),
            "test_p2p_consumer.py": (
                "import consumer_03\n\n\n"
                "def test_consumer():\n"
                "    assert consumer_03.VAL == 'sk-old'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "long_context_008",
        "category": "long_context",
        "kind": "code_change",
        "title": "跨模块状态一致",
        "prompt": (
            "config_a.py 和 config_b.py 都定义了 VERSION，但值不一致（一个 1.0 一个 2.0）。\n"
            "main.py 同时用到两者。请统一为 2.0，保证两个模块一致。"
        ),
        "setup": {
            "config_a.py": "VERSION = '1.0'\n",
            "config_b.py": "VERSION = '2.0'\n",
            "main.py": (
                "from config_a import VERSION as VA\n"
                "from config_b import VERSION as VB\n\n\n"
                "def versions():\n"
                "    return VA, VB\n"
            ),
        },
        "tests": {
            "test_f2p_consistent.py": (
                "from config_a import VERSION as VA\n"
                "from config_b import VERSION as VB\n\n\n"
                "def test_consistent():\n"
                "    assert VA == VB == '2.0'\n"
            ),
            "test_p2p_main.py": (
                "import main\n\n\n"
                "def test_main():\n"
                "    assert main.versions() == ('2.0', '2.0')\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "long_context_009",
        "category": "long_context",
        "kind": "code_change",
        "title": "拆分长函数为子函数",
        "prompt": (
            "pipeline.py 的 process(data) 包含两步：先 strip 清洗，再过滤空串。\n"
            "把它拆成 step1(data) 和 step2(cleaned) 两个子函数，process 调用它们。"
        ),
        "setup": {
            "pipeline.py": (
                "def process(data):\n"
                "    cleaned = [x.strip() for x in data]\n"
                "    filtered = [x for x in cleaned if x]\n"
                "    return filtered\n"
            ),
        },
        "tests": {
            "test_f2p_steps.py": (
                "from pipeline import step1, step2\n\n\n"
                "def test_step1():\n"
                "    assert step1([' a ', ' b ']) == ['a', 'b']\n\n\n"
                "def test_step2():\n"
                "    assert step2(['a', '', 'b']) == ['a', 'b']\n"
            ),
            "test_p2p_process.py": (
                "from pipeline import process\n\n\n"
                "def test_process():\n"
                "    assert process([' a ', '', ' b ']) == ['a', 'b']\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "long_context_010",
        "category": "long_context",
        "kind": "code_change",
        "title": "多文件配置一致",
        "prompt": (
            "有 dev.yaml、staging.yaml、prod.yaml 三个配置文件，它们的 port 分别是 8000、8001、8002。\n"
            "请统一为 8000（只改 staging 和 prod）。"
        ),
        "setup": {
            "dev.yaml": "port: 8000\n",
            "staging.yaml": "port: 8001\n",
            "prod.yaml": "port: 8002\n",
        },
        "tests": {
            "test_f2p_config.py": (
                "import yaml\n\n\n"
                "def test_staging():\n"
                "    d = yaml.safe_load(open('staging.yaml'))\n"
                "    assert d['port'] == 8000\n\n\n"
                "def test_prod():\n"
                "    d = yaml.safe_load(open('prod.yaml'))\n"
                "    assert d['port'] == 8000\n"
            ),
            "test_p2p_dev.py": (
                "import yaml\n\n\n"
                "def test_dev():\n"
                "    d = yaml.safe_load(open('dev.yaml'))\n"
                "    assert d['port'] == 8000\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    # =====================================================================
    # refactor —— 重构 / 多文件综合（5 题）
    # =====================================================================
    {
        "id": "refactor_001",
        "category": "refactor",
        "kind": "code_change",
        "title": "抽取重复代码到公共模块",
        "prompt": (
            "a.py 和 b.py 里都有一个 process 函数，实现完全相同。\n"
            "把它抽取到新建的 shared.py，并让 a.py、b.py 从 shared 导入。"
        ),
        "setup": {
            "a.py": "def process(x):\n    return x.strip().upper()\n",
            "b.py": "def process(x):\n    return x.strip().upper()\n",
        },
        "tests": {
            "test_f2p_shared.py": (
                "from shared import process\n\n\n"
                "def test_shared():\n"
                "    assert process(' hi ') == 'HI'\n"
            ),
            "test_p2p_ab.py": (
                "from a import process as pa\n"
                "from b import process as pb\n\n\n"
                "def test_a():\n"
                "    assert pa(' x ') == 'X'\n\n\n"
                "def test_b():\n"
                "    assert pb(' y ') == 'Y'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "refactor_002",
        "category": "refactor",
        "kind": "code_change",
        "title": "移动函数到新模块",
        "prompt": (
            "utils.py 里有 helper_a 和 helper_b 两个函数。\n"
            "把 helper_b 移到新建的 newmod.py，并在 utils.py 里通过导入保持可访问。"
        ),
        "setup": {
            "utils.py": (
                "def helper_a():\n    return 'a'\n\n\n"
                "def helper_b():\n    return 'b'\n"
            ),
        },
        "tests": {
            "test_f2p_move.py": (
                "from newmod import helper_b\n\n\n"
                "def test_newmod():\n"
                "    assert helper_b() == 'b'\n"
            ),
            "test_p2p_utils.py": (
                "from utils import helper_a, helper_b\n\n\n"
                "def test_helper_a():\n"
                "    assert helper_a() == 'a'\n\n\n"
                "def test_helper_b_reachable():\n"
                "    assert helper_b() == 'b'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "refactor_003",
        "category": "refactor",
        "kind": "code_change",
        "title": "抽取基类消除重复",
        "prompt": (
            "cat.py 和 dog.py 里各有一个 speak 方法，逻辑类似。\n"
            "抽取一个 Animal 基类（放在 animal.py），让 Cat、Dog 继承它。"
        ),
        "setup": {
            "animal.py": "",
            "cat.py": "class Cat:\n    def speak(self):\n        return 'meow'\n",
            "dog.py": "class Dog:\n    def speak(self):\n        return 'woof'\n",
        },
        "tests": {
            "test_f2p_base.py": (
                "from animal import Animal\n"
                "from cat import Cat\n"
                "from dog import Dog\n\n\n"
                "def test_inheritance():\n"
                "    assert issubclass(Cat, Animal)\n"
                "    assert issubclass(Dog, Animal)\n"
            ),
            "test_p2p_speak.py": (
                "from cat import Cat\n"
                "from dog import Dog\n\n\n"
                "def test_cat():\n"
                "    assert Cat().speak() == 'meow'\n\n\n"
                "def test_dog():\n"
                "    assert Dog().speak() == 'woof'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "refactor_004",
        "category": "refactor",
        "kind": "code_change",
        "title": "消除魔法数字",
        "prompt": (
            "code.py 里多处硬编码了 3.14。请定义常量 PI = 3.14，并让所有函数改用 PI。"
        ),
        "setup": {
            "code.py": (
                "def area(r):\n    return 3.14 * r * r\n\n\n"
                "def circumference(r):\n    return 2 * 3.14 * r\n"
            ),
        },
        "tests": {
            "test_f2p_pi.py": (
                "import code\n\n\n"
                "def test_pi_defined():\n"
                "    assert code.PI == 3.14\n\n\n"
                "def test_area():\n"
                "    assert code.area(1) == 3.14\n"
            ),
            "test_p2p_circumference.py": (
                "import code\n\n\n"
                "def test_circumference():\n"
                "    assert code.circumference(1) == 6.28\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "refactor_005",
        "category": "refactor",
        "kind": "code_change",
        "title": "解耦全局可变状态",
        "prompt": (
            "settings.py 里有一个模块级可变字典 config，两个函数直接修改它。\n"
            "改为提供一个 get_config() 函数返回 config 的副本，让外部修改不会影响原值。"
        ),
        "setup": {
            "settings.py": (
                "config = {'debug': False}\n\n\n"
                "def get_config():\n    return config\n"
            ),
        },
        "tests": {
            "test_f2p_copy.py": (
                "import settings\n\n\n"
                "def test_returns_copy():\n"
                "    c = settings.get_config()\n"
                "    c['debug'] = True\n"
                "    assert settings.get_config()['debug'] is False\n"
            ),
            "test_p2p_value.py": (
                "import settings\n\n\n"
                "def test_default():\n"
                "    assert settings.get_config()['debug'] is False\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    # <<<MORE_TASKS>>>
]


def _write_task(t: dict[str, Any]) -> None:
    """写 tasks/<id>.yaml 与 datasets/<category>/<id>/{setup,tests}/。"""
    task_id = t["id"]
    category = t["category"]

    # 任务定义
    yaml_body = {
        "id": task_id,
        "category": category,
        "kind": t["kind"],
        "title": t["title"],
        "prompt": t["prompt"],
        "timeout_seconds": t.get("timeout_seconds", 300),
        "max_retries": t.get("max_retries", 0),
        "grader": t["grader"],
    }
    task_file = TASKS_DIR / f"{task_id}.yaml"
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text(
        yaml.safe_dump(yaml_body, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    # 题面数据
    base = DATASETS_DIR / category / task_id
    for sub in ("setup", "tests"):
        (base / sub).mkdir(parents=True, exist_ok=True)
    for rel, content in t["setup"].items():
        f = base / "setup" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    for rel, content in t["tests"].items():
        f = base / "tests" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")


def main() -> None:
    ids = [t["id"] for t in TASKS]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate task id detected")

    # 幂等：清空旧的生成产物，避免残留被删任务
    for d in (TASKS_DIR, DATASETS_DIR):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)

    for t in TASKS:
        _write_task(t)

    counts: dict[str, int] = {}
    for t in TASKS:
        counts[t["category"]] = counts.get(t["category"], 0) + 1
    print(f"generated {len(TASKS)} tasks: {counts}")


if __name__ == "__main__":
    main()
