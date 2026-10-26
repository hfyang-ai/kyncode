"""数据驱动生成 40 道 KynCode 高难度测评任务（幂等，可重复执行）。

产物：
- tasks/<task_id>.yaml         任务定义（prompt + 判分配置 + difficulty）
- datasets/<category>/<task_id>/setup/  初始代码
- datasets/<category>/<task_id>/tests/  隐藏测试（test_f2p_*.py / test_p2p_*.py）

任务分布（全 difficulty=hard）：
- bugfix 4          多文件复合 bug 定位根因（跨层类型污染 / 状态机 / 引用泄漏 / 递归边界）
- feature 4         补全非平凡实现（任务调度器 / 配置合并 / 事件总线 / semver 比较）
- refactor 3        跨文件重构保行为（策略模式 / 过程式转类 / 拆模块消循环依赖）
- debug 4           失败测试驱动修复（快排分区 / 日期跨度 / 记忆化状态泄漏 / 编码）
- tool_recovery 3   导入/环境错误恢复（三方循环导入 / 包内相对导入 / 弃用 API shim）
- long_task 2       长任务（多模块迷你框架 + 长流水线交叉依赖）
- algorithm 4       算法实现（拓扑排序 / 并查集 / 滑动窗口 / 区间合并）
- concurrency 4     并发（线程安全计数器 / 线程安全 KV / asyncio 聚合 / 生产者消费者）
- parsing 4         解析（表达式求值 / INI / 状态机 / CSV）
- optimization 4    性能（两数之和 / 前缀和 / 斐波那契 / 有序去重，大数据量约束复杂度）
- permission 2      权限规则约束下完成任务（deny 写受保护文件 / deny 读敏感文件）
- hook 2            钩子约束下完成任务（reject Bash / reject WriteFile）

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


def _stages(n_stages: int, bug: int | list[int] | None = None, cross: bool = False) -> dict[str, str]:
    """生成 n 个 stage 模块 + pipeline.py 组成的线性流水线。

    每个 stage_XX.py 定义 transform(x)，正确行为是 x + XX。
    bug 指定错误环节（int 或 list[int]），该环节 transform 写成 x * 2。
    cross=True 时额外生成 cross.py，聚合 stage_00 与最后一个 stage。
    """
    bugs: set[int] = set()
    if isinstance(bug, int):
        bugs = {bug}
    elif bug:
        bugs = set(bug)

    files: dict[str, str] = {}
    for i in range(n_stages):
        if i in bugs:
            body = "def transform(x):\n    return x * 2\n"
        else:
            body = f"def transform(x):\n    return x + {i}\n"
        files[f"stage_{i:02d}.py"] = body

    imports = "\n".join(
        f"from stage_{i:02d} import transform as _t{i}" for i in range(n_stages)
    )
    steps = "\n".join(f"    x = _t{i}(x)" for i in range(n_stages))
    files["pipeline.py"] = (
        imports + "\n\n\n" + "def run(x):\n" + steps + "\n    return x\n"
    )

    if cross:
        files["cross.py"] = (
            "from stage_00 import transform as t0\n"
            f"from stage_{n_stages - 1:02d} import transform as tlast\n\n\n"
            "def aggregate(x):\n"
            "    return t0(x) + tlast(x)\n"
        )
    return files


# 每个任务：setup/tests 是 {文件名: 内容} 映射。
TASKS: list[dict[str, Any]] = [
    # =====================================================================
    # bugfix —— 多文件复合 bug，跨层追根（4 题）
    # =====================================================================
    {
        "id": "bugfix_001",
        "category": "bugfix",
        "kind": "code_change",
        "title": "跨层类型污染导致静默错误",
        "difficulty": "hard",
        "prompt": (
            "用户反馈：订单总价算出来明显不对，但程序没有报任何错误。\n"
            "相关模块：inventory（库存定价）→ pricing（计价）→ order（汇总）。\n"
            "请排查并修复根因，使订单总价返回正确的数值。"
        ),
        "setup": {
            "inventory.py": (
                "def get_price(sku):\n"
                "    return '20'\n\n\n"
                "def get_name(sku):\n"
                "    return 'Widget'\n"
            ),
            "pricing.py": (
                "from inventory import get_price\n\n\n"
                "def line_total(sku, qty):\n"
                "    return get_price(sku) * qty\n\n\n"
                "def format_currency(amount):\n"
                "    return f'${amount:.2f}'\n"
            ),
            "order.py": (
                "from pricing import line_total\n\n\n"
                "def order_total(items):\n"
                "    total = 0\n"
                "    for sku, qty in items:\n"
                "        total += line_total(sku, qty)\n"
                "    return total\n"
            ),
        },
        "tests": {
            "test_f2p_total.py": (
                "from order import order_total\n\n\n"
                "def test_two_items():\n"
                "    assert order_total([('a', 2), ('b', 1)]) == 60\n\n\n"
                "def test_single():\n"
                "    assert order_total([('a', 1)]) == 20\n"
            ),
            "test_p2p_misc.py": (
                "from pricing import format_currency\n"
                "from inventory import get_name\n\n\n"
                "def test_format():\n"
                "    assert format_currency(20) == '$20.00'\n\n\n"
                "def test_name():\n"
                "    assert get_name('a') == 'Widget'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "bugfix_002",
        "category": "bugfix",
        "kind": "code_change",
        "title": "订单状态机非法转换未拦截",
        "difficulty": "hard",
        "prompt": (
            "订单系统有状态机：pending → paid → shipped → delivered。\n"
            "业务规则：只能沿这条路径前进；cancel 只允许在 pending 或 paid 状态；"
            "已 delivered 的订单不能再做任何转换。\n"
            "用户反馈：一个已经 delivered 的订单居然还能被 cancel。\n"
            "请排查状态机的转换逻辑并修复，使所有非法转换都被拒绝。"
        ),
        "setup": {
            "order_state.py": (
                "ALLOWED = {\n"
                "    'pending': ['paid', 'cancel'],\n"
                "    'paid': ['shipped', 'cancel'],\n"
                "    'shipped': ['delivered'],\n"
                "    'delivered': [],\n"
                "    'cancel': [],\n"
                "}\n\n\n"
                "def can_transition(from_state, to_state):\n"
                "    return to_state in ALLOWED.get(from_state, [])\n\n\n"
                "def display(state):\n"
                "    return state.upper()\n"
            ),
            "order.py": (
                "from order_state import can_transition\n\n\n"
                "def advance(state, target):\n"
                "    if can_transition(state, target):\n"
                "        return target\n"
                "    return state\n\n\n"
                "def cancel(state):\n"
                "    if state != 'delivered':\n"
                "        return 'cancel'\n"
                "    return state\n"
            ),
        },
        "tests": {
            "test_f2p_transition.py": (
                "from order import advance, cancel\n\n\n"
                "def test_legal_path():\n"
                "    assert advance('pending', 'paid') == 'paid'\n"
                "    assert advance('paid', 'shipped') == 'shipped'\n\n\n"
                "def test_delivered_cannot_cancel():\n"
                "    assert cancel('delivered') == 'delivered'\n\n\n"
                "def test_shipped_cannot_cancel():\n"
                "    assert cancel('shipped') == 'shipped'\n\n\n"
                "def test_illegal_advance_rejected():\n"
                "    assert advance('pending', 'shipped') == 'pending'\n"
                "    assert advance('cancel', 'paid') == 'cancel'\n"
            ),
            "test_p2p_display.py": (
                "from order_state import display\n\n\n"
                "def test_display():\n"
                "    assert display('pending') == 'PENDING'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "bugfix_003",
        "category": "bugfix",
        "kind": "code_change",
        "title": "缓存返回内部引用被外部篡改",
        "difficulty": "hard",
        "prompt": (
            "配置中心维护一个配置字典，get() 会缓存读取结果。\n"
            "用户反馈：某个调用方修改了拿到的配置后，其他调用方读到的值也被污染了。\n"
            "请排查引用/拷贝问题并修复，使每次 get() 返回独立副本、互不影响。"
        ),
        "setup": {
            "config_store.py": (
                "_cache = {}\n\n\n"
                "def load(key):\n"
                "    return {'name': 'svc', 'tags': ['a', 'b']}\n\n\n"
                "def get(key):\n"
                "    if key not in _cache:\n"
                "        _cache[key] = load(key)\n"
                "    return _cache[key]\n"
            ),
            "consumer.py": (
                "from config_store import get\n\n\n"
                "def snapshot(key):\n"
                "    cfg = get(key)\n"
                "    return {'name': cfg['name'], 'tags': list(cfg['tags'])}\n"
            ),
        },
        "tests": {
            "test_f2p_isolation.py": (
                "from config_store import get\n\n\n"
                "def test_no_pollution():\n"
                "    a = get('svc')\n"
                "    a['tags'].append('polluted')\n"
                "    b = get('svc')\n"
                "    assert b['tags'] == ['a', 'b']\n\n\n"
                "def test_top_level_independent():\n"
                "    a = get('svc')\n"
                "    a['name'] = 'changed'\n"
                "    b = get('svc')\n"
                "    assert b['name'] == 'svc'\n"
            ),
            "test_p2p_consumer.py": (
                "from consumer import snapshot\n\n\n"
                "def test_snapshot():\n"
                "    s = snapshot('svc')\n"
                "    assert s['name'] == 'svc'\n"
                "    assert s['tags'] == ['a', 'b']\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "bugfix_004",
        "category": "bugfix",
        "kind": "code_change",
        "title": "目录统计漏算子目录",
        "difficulty": "hard",
        "prompt": (
            "filesystem.total_size 用于递归统计一个目录树里所有文件的大小之和。\n"
            "用户反馈：包含嵌套子目录的目录统计结果偏小。\n"
            "请排查递归遍历逻辑并修复，正确处理多层嵌套目录。"
        ),
        "setup": {
            "filesystem.py": (
                "def walk(entries):\n"
                "    for entry in entries:\n"
                "        if entry['type'] == 'file':\n"
                "            yield entry\n\n\n"
                "def total_size(entries):\n"
                "    files = walk(entries)\n"
                "    return sum(f['size'] for f in files)\n\n\n"
                "def count_files(entries):\n"
                "    return sum(1 for _ in walk(entries))\n"
            ),
        },
        "tests": {
            "test_f2p_size.py": (
                "from filesystem import total_size\n\n\n"
                "def test_nested():\n"
                "    tree = [\n"
                "        {'type': 'dir', 'children': [\n"
                "            {'type': 'file', 'size': 10},\n"
                "            {'type': 'dir', 'children': [{'type': 'file', 'size': 5}]},\n"
                "        ]},\n"
                "        {'type': 'file', 'size': 7},\n"
                "    ]\n"
                "    assert total_size(tree) == 22\n\n\n"
                "def test_empty_dir():\n"
                "    assert total_size([{'type': 'dir', 'children': []}]) == 0\n"
            ),
            "test_p2p_count.py": (
                "from filesystem import count_files\n\n\n"
                "def test_count():\n"
                "    tree = [{'type': 'file', 'size': 1}, {'type': 'file', 'size': 2}]\n"
                "    assert count_files(tree) == 2\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    # =====================================================================
    # feature —— 补全非平凡实现（4 题）
    # =====================================================================
    {
        "id": "feature_001",
        "category": "feature",
        "kind": "code_change",
        "title": "实现任务调度器（拓扑排序）",
        "difficulty": "hard",
        "prompt": (
            "scheduler.py 里的 schedule 还没有实现。请实现它：\n"
            "输入是任务列表 tasks（每个是 (name, [依赖任务名])）和依赖关系。\n"
            "返回一个合法的执行顺序列表；若存在循环依赖，抛 ValueError。\n"
            "不要改动已经实现的 has_cycle 函数。"
        ),
        "setup": {
            "scheduler.py": (
                "def has_cycle(tasks):\n"
                "    return False\n\n\n"
                "def schedule(tasks):\n"
                "    raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_schedule.py": (
                "import pytest\n"
                "from scheduler import schedule\n\n\n"
                "def test_linear():\n"
                "    tasks = {'a': [], 'b': ['a'], 'c': ['b']}\n"
                "    order = schedule(tasks)\n"
                "    assert order.index('a') < order.index('b') < order.index('c')\n\n\n"
                "def test_branch():\n"
                "    tasks = {'a': [], 'b': ['a'], 'c': ['a'], 'd': ['b', 'c']}\n"
                "    order = schedule(tasks)\n"
                "    assert order.index('a') < order.index('d')\n"
                "    assert order.index('b') < order.index('d')\n"
                "    assert order.index('c') < order.index('d')\n\n\n"
                "def test_cycle():\n"
                "    tasks = {'a': ['b'], 'b': ['a']}\n"
                "    with pytest.raises(ValueError):\n"
                "        schedule(tasks)\n"
            ),
            "test_p2p_has_cycle.py": (
                "from scheduler import has_cycle\n\n\n"
                "def test_has_cycle_false():\n"
                "    assert has_cycle({}) is False\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "feature_002",
        "category": "feature",
        "kind": "code_change",
        "title": "实现配置分层合并",
        "difficulty": "hard",
        "prompt": (
            "configmerge.py 里的 merge_configs 还没有实现。请实现它：\n"
            "接收任意数量的配置字典（越靠后优先级越高），深度合并：\n"
            "嵌套字典递归合并，非字典值用后者覆盖，返回全新字典且不改动入参。\n"
            "不要改动已经实现的 to_env 函数。"
        ),
        "setup": {
            "configmerge.py": (
                "def to_env(prefix, cfg):\n"
                "    return {f'{prefix}_{k}': v for k, v in cfg.items()}\n\n\n"
                "def merge_configs(*configs):\n"
                "    raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_merge.py": (
                "from configmerge import merge_configs\n\n\n"
                "def test_deep():\n"
                "    base = {'db': {'host': 'localhost', 'port': 5432}, 'debug': False}\n"
                "    prod = {'db': {'host': 'db.prod', 'pool': 10}, 'debug': True}\n"
                "    merged = merge_configs(base, prod)\n"
                "    assert merged == {'db': {'host': 'db.prod', 'port': 5432, 'pool': 10}, 'debug': True}\n\n\n"
                "def test_no_mutate():\n"
                "    base = {'db': {'host': 'a'}}\n"
                "    override = {'db': {'port': 1}}\n"
                "    merge_configs(base, override)\n"
                "    assert base == {'db': {'host': 'a'}}\n\n\n"
                "def test_three_layers():\n"
                "    a = {'x': 1}\n"
                "    b = {'x': 2, 'y': 3}\n"
                "    c = {'z': 4}\n"
                "    assert merge_configs(a, b, c) == {'x': 2, 'y': 3, 'z': 4}\n"
            ),
            "test_p2p_to_env.py": (
                "from configmerge import to_env\n\n\n"
                "def test_to_env():\n"
                "    assert to_env('APP', {'a': 1}) == {'APP_a': 1}\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "feature_003",
        "category": "feature",
        "kind": "code_change",
        "title": "实现事件总线",
        "difficulty": "hard",
        "prompt": (
            "bus.py 里的 EventBus 还没有实现。请实现它：\n"
            "- on(event, handler) 注册处理器，返回取消订阅函数\n"
            "- once(event, handler) 只触发一次的处理器\n"
            "- emit(event, *args) 触发该事件所有处理器，按注册顺序调用\n"
            "不要改动已经实现的 normalize 函数。"
        ),
        "setup": {
            "bus.py": (
                "def normalize(event):\n"
                "    return event.strip().lower()\n\n\n"
                "class EventBus:\n"
                "    def __init__(self):\n"
                "        raise NotImplementedError\n\n"
                "    def on(self, event, handler):\n"
                "        raise NotImplementedError\n\n"
                "    def once(self, event, handler):\n"
                "        raise NotImplementedError\n\n"
                "    def emit(self, event, *args):\n"
                "        raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_bus.py": (
                "from bus import EventBus\n\n\n"
                "def test_on_emit():\n"
                "    b = EventBus()\n"
                "    got = []\n"
                "    b.on('save', lambda x: got.append(x))\n"
                "    b.emit('save', 42)\n"
                "    assert got == [42]\n\n\n"
                "def test_once():\n"
                "    b = EventBus()\n"
                "    got = []\n"
                "    b.once('init', lambda: got.append(1))\n"
                "    b.emit('init')\n"
                "    b.emit('init')\n"
                "    assert got == [1]\n\n\n"
                "def test_unsubscribe():\n"
                "    b = EventBus()\n"
                "    got = []\n"
                "    off = b.on('tick', lambda: got.append(1))\n"
                "    off()\n"
                "    b.emit('tick')\n"
                "    assert got == []\n\n\n"
                "def test_order():\n"
                "    b = EventBus()\n"
                "    order = []\n"
                "    b.on('x', lambda: order.append(1))\n"
                "    b.on('x', lambda: order.append(2))\n"
                "    b.emit('x')\n"
                "    assert order == [1, 2]\n"
            ),
            "test_p2p_normalize.py": (
                "from bus import normalize\n\n\n"
                "def test_normalize():\n"
                "    assert normalize('  Save ') == 'save'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "feature_004",
        "category": "feature",
        "kind": "code_change",
        "title": "实现语义化版本比较器",
        "difficulty": "hard",
        "prompt": (
            "semver.py 里的 compare 还没有实现。请实现它：\n"
            "比较两个语义化版本字符串 'major.minor.patch'，返回 -1/0/1。\n"
            "不要改动已经实现的 parse 函数。"
        ),
        "setup": {
            "semver.py": (
                "def parse(version):\n"
                "    return tuple(int(x) for x in version.split('.'))\n\n\n"
                "def compare(v1, v2):\n"
                "    raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_compare.py": (
                "from semver import compare\n\n\n"
                "def test_equal():\n"
                "    assert compare('1.2.3', '1.2.3') == 0\n\n\n"
                "def test_less():\n"
                "    assert compare('1.2.3', '1.2.4') == -1\n"
                "    assert compare('1.9.0', '2.0.0') == -1\n\n\n"
                "def test_greater():\n"
                "    assert compare('2.0.0', '1.9.9') == 1\n"
                "    assert compare('1.2.10', '1.2.9') == 1\n"
            ),
            "test_p2p_parse.py": (
                "from semver import parse\n\n\n"
                "def test_parse():\n"
                "    assert parse('10.20.30') == (10, 20, 30)\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    # =====================================================================
    # refactor —— 跨文件重构保行为（3 题）
    # =====================================================================
    {
        "id": "refactor_001",
        "category": "refactor",
        "kind": "code_change",
        "title": "抽模板方法基类消除重复",
        "difficulty": "hard",
        "prompt": (
            "payments.py 里三个支付类（Credit/Debit/Crypto）都重复实现了相同的\n"
            "validate 逻辑和 process 骨架。请在 payments.py 里抽取一个\n"
            "PaymentProcessor 基类（含 validate 和模板方法 process），\n"
            "三个子类继承它并只实现各自的 _pay 钩子，保持对外行为不变。"
        ),
        "setup": {
            "payments.py": (
                "class CreditPayment:\n"
                "    def validate(self, amount):\n"
                "        if amount <= 0:\n"
                "            raise ValueError('amount must be positive')\n\n"
                "    def process(self, amount):\n"
                "        self.validate(amount)\n"
                "        return f'credit:{amount}'\n\n\n"
                "class DebitPayment:\n"
                "    def validate(self, amount):\n"
                "        if amount <= 0:\n"
                "            raise ValueError('amount must be positive')\n\n"
                "    def process(self, amount):\n"
                "        self.validate(amount)\n"
                "        return f'debit:{amount}'\n\n\n"
                "class CryptoPayment:\n"
                "    def validate(self, amount):\n"
                "        if amount <= 0:\n"
                "            raise ValueError('amount must be positive')\n\n"
                "    def process(self, amount):\n"
                "        self.validate(amount)\n"
                "        return f'crypto:{amount}'\n"
            ),
        },
        "tests": {
            "test_f2p_base.py": (
                "import pytest\n"
                "from payments import PaymentProcessor, CreditPayment, DebitPayment, CryptoPayment\n\n\n"
                "def test_inheritance():\n"
                "    assert issubclass(CreditPayment, PaymentProcessor)\n"
                "    assert issubclass(DebitPayment, PaymentProcessor)\n"
                "    assert issubclass(CryptoPayment, PaymentProcessor)\n\n\n"
                "def test_base_validate():\n"
                "    with pytest.raises(ValueError):\n"
                "        CreditPayment().process(0)\n\n\n"
                "def test_process_via_base():\n"
                "    assert CreditPayment().process(10) == 'credit:10'\n"
            ),
            "test_p2p_behavior.py": (
                "from payments import DebitPayment, CryptoPayment\n\n\n"
                "def test_debit():\n"
                "    assert DebitPayment().process(5) == 'debit:5'\n\n\n"
                "def test_crypto():\n"
                "    assert CryptoPayment().process(7) == 'crypto:7'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "refactor_002",
        "category": "refactor",
        "kind": "code_change",
        "title": "过程式代码重构为类并向后兼容",
        "difficulty": "hard",
        "prompt": (
            "stats.py 用模块级变量和多个函数维护统计数据，状态容易互相污染。\n"
            "请重构为 Stats 类（add(n) / mean() / count()），用实例封装状态；\n"
            "同时保留模块级的 add/mean/count 函数作为默认实例的便捷入口（向后兼容）。"
        ),
        "setup": {
            "stats.py": (
                "_total = 0\n"
                "_count = 0\n\n\n"
                "def add(n):\n"
                "    global _total, _count\n"
                "    _total += n\n"
                "    _count += 1\n\n\n"
                "def mean():\n"
                "    return _total / _count if _count else 0\n\n\n"
                "def count():\n"
                "    return _count\n"
            ),
        },
        "tests": {
            "test_f2p_class.py": (
                "from stats import Stats\n\n\n"
                "def test_isolated_instances():\n"
                "    a = Stats()\n"
                "    b = Stats()\n"
                "    a.add(10)\n"
                "    a.add(20)\n"
                "    b.add(100)\n"
                "    assert a.mean() == 15\n"
                "    assert b.mean() == 100\n\n\n"
                "def test_count():\n"
                "    s = Stats()\n"
                "    s.add(1)\n"
                "    s.add(2)\n"
                "    assert s.count() == 2\n\n\n"
                "def test_empty_mean():\n"
                "    assert Stats().mean() == 0\n"
            ),
            "test_p2p_backcompat.py": (
                "from stats import add, mean, count\n\n\n"
                "def test_module_funcs():\n"
                "    s = count()\n"
                "    add(4)\n"
                "    assert count() == s + 1\n"
                "    assert mean() == 4\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "refactor_003",
        "category": "refactor",
        "kind": "code_change",
        "title": "拆巨型模块并消除循环依赖",
        "difficulty": "hard",
        "prompt": (
            "app.py 同时定义了用户相关和订单相关的函数，且 user.py 与 order.py\n"
            "之间互相 import 形成了循环依赖。\n"
            "请：1) 把 app.py 里的函数拆分到 users.py 和 orders.py；\n"
            "2) 解除 user/order 之间的循环依赖；\n"
            "3) 让 app.py 通过 re-export 保持原有 from app import ... 的用法不变。"
        ),
        "setup": {
            "app.py": (
                "from user import get_username\n"
                "from order import get_order_user\n\n\n"
                "def user_label(uid):\n"
                "    return f'user:{get_username(uid)}'\n\n\n"
                "def order_label(oid):\n"
                "    return f'order:{get_order_user(oid)}'\n"
            ),
            "user.py": (
                "from order import get_order_user\n\n\n"
                "def get_username(uid):\n"
                "    return f'u{uid}'\n\n\n"
                "def orders_of(uid):\n"
                "    return get_order_user(uid)\n"
            ),
            "order.py": (
                "from user import get_username\n\n\n"
                "def get_order_user(oid):\n"
                "    return f'user-of-{oid}'\n"
            ),
        },
        "tests": {
            "test_f2p_split.py": (
                "from users import user_label\n"
                "from orders import order_label\n\n\n"
                "def test_users():\n"
                "    assert user_label(1) == 'user:u1'\n\n\n"
                "def test_orders():\n"
                "    assert order_label(2) == 'order:user-of-2'\n\n\n"
                "def test_no_cycle_import():\n"
                "    import user\n"
                "    import order\n"
                "    assert user.get_username(1) == 'u1'\n"
                "    assert order.get_order_user(2) == 'user-of-2'\n"
            ),
            "test_p2p_backcompat.py": (
                "from app import user_label, order_label\n\n\n"
                "def test_backcompat():\n"
                "    assert user_label(3) == 'user:u3'\n"
                "    assert order_label(4) == 'order:user-of-4'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    # =====================================================================
    # debug —— 失败测试驱动修复（4 题）
    # =====================================================================
    {
        "id": "debug_001",
        "category": "debug",
        "kind": "code_change",
        "title": "修复快速排序分区边界",
        "difficulty": "hard",
        "prompt": (
            "运行 pytest 会发现 test_work.py 失败：quicksort 对某些数组排序错误或递归不终止。\n"
            "阅读失败信息，定位分区逻辑的边界问题并修复。"
        ),
        "setup": {
            "work.py": (
                "def quicksort(arr):\n"
                "    if len(arr) <= 1:\n"
                "        return arr\n"
                "    pivot = arr[0]\n"
                "    left = [x for x in arr if x < pivot]\n"
                "    right = [x for x in arr if x > pivot]\n"
                "    return quicksort(left) + [pivot] + quicksort(right)\n"
            ),
            "test_work.py": (
                "from work import quicksort\n\n\n"
                "def test_duplicates():\n"
                "    assert quicksort([3, 1, 2, 1, 3]) == [1, 1, 2, 3, 3]\n"
            ),
        },
        "tests": {
            "test_f2p_sort.py": (
                "from work import quicksort\n\n\n"
                "def test_duplicates():\n"
                "    assert quicksort([3, 1, 2, 1, 3]) == [1, 1, 2, 3, 3]\n\n\n"
                "def test_sorted():\n"
                "    assert quicksort([1, 2, 3, 4]) == [1, 2, 3, 4]\n\n\n"
                "def test_reversed():\n"
                "    assert quicksort([5, 4, 3, 2, 1]) == [1, 2, 3, 4, 5]\n\n\n"
                "def test_single():\n"
                "    assert quicksort([9]) == [9]\n"
            ),
            "test_p2p_sort.py": (
                "from work import quicksort\n\n\n"
                "def test_empty():\n"
                "    assert quicksort([]) == []\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "debug_002",
        "category": "debug",
        "kind": "code_change",
        "title": "修复闰年二月天数",
        "difficulty": "hard",
        "prompt": (
            "运行 pytest 会发现 test_work.py 失败：days_in_month 对平年 2 月返回了 29 天。\n"
            "阅读失败信息，修复二月天数的判断（平年 28 天、闰年 29 天，注意整百年规则）。"
        ),
        "setup": {
            "work.py": (
                "def days_in_month(year, month):\n"
                "    if month in (4, 6, 9, 11):\n"
                "        return 30\n"
                "    if month == 2:\n"
                "        return 29\n"
                "    return 31\n\n\n"
                "def days_between(y1, m1, d1, y2, m2, d2):\n"
                "    from datetime import date\n"
                "    return (date(y2, m2, d2) - date(y1, m1, d1)).days\n"
            ),
            "test_work.py": (
                "from work import days_in_month\n\n\n"
                "def test_feb_common():\n"
                "    assert days_in_month(2023, 2) == 28\n"
            ),
        },
        "tests": {
            "test_f2p_days.py": (
                "from work import days_in_month\n\n\n"
                "def test_feb_common():\n"
                "    assert days_in_month(2023, 2) == 28\n\n\n"
                "def test_feb_leap():\n"
                "    assert days_in_month(2024, 2) == 29\n\n\n"
                "def test_century():\n"
                "    assert days_in_month(2000, 2) == 29\n"
                "    assert days_in_month(1900, 2) == 28\n"
            ),
            "test_p2p_days.py": (
                "from work import days_in_month, days_between\n\n\n"
                "def test_thirty():\n"
                "    assert days_in_month(2024, 4) == 30\n\n\n"
                "def test_thirty_one():\n"
                "    assert days_in_month(2024, 1) == 31\n\n\n"
                "def test_days_between():\n"
                "    assert days_between(2024, 1, 1, 2024, 1, 10) == 9\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "debug_003",
        "category": "debug",
        "kind": "code_change",
        "title": "修复贪心找零算法",
        "difficulty": "hard",
        "prompt": (
            "运行 pytest 会发现 test_work.py 失败：make_change 对某些金额返回的硬币数不是最少的。\n"
            "阅读失败信息，修复找零算法，使它始终返回数量最少的硬币组合。"
        ),
        "setup": {
            "work.py": (
                "def make_change(coins, amount):\n"
                "    coins = sorted(coins, reverse=True)\n"
                "    result = []\n"
                "    for c in coins:\n"
                "        while amount >= c:\n"
                "            result.append(c)\n"
                "            amount -= c\n"
                "    return result\n"
            ),
            "test_work.py": (
                "from work import make_change\n\n\n"
                "def test_min_coins():\n"
                "    assert len(make_change([1, 5, 7], 10)) == 2\n"
            ),
        },
        "tests": {
            "test_f2p_change.py": (
                "from work import make_change\n\n\n"
                "def test_min_coins():\n"
                "    assert len(make_change([1, 5, 7], 10)) == 2\n\n\n"
                "def test_another_min():\n"
                "    assert len(make_change([1, 3, 4], 6)) == 2\n\n\n"
                "def test_sum_correct():\n"
                "    coins = make_change([1, 2, 5], 11)\n"
                "    assert sum(coins) == 11\n"
                "    assert len(coins) == 3\n\n\n"
                "def test_single_coin():\n"
                "    assert make_change([1], 3) == [1, 1, 1]\n"
            ),
            "test_p2p_change.py": (
                "from work import make_change\n\n\n"
                "def test_exact_fit():\n"
                "    assert make_change([1, 5, 10], 10) == [10]\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "debug_004",
        "category": "debug",
        "kind": "code_change",
        "title": "修复字节解码编码错误",
        "difficulty": "hard",
        "prompt": (
            "运行 pytest 会发现 test_work.py 失败：decode_utf8 处理非法字节时报错或返回错误结果。\n"
            "阅读失败信息，修复解码逻辑（对无法解码的字节应能容错处理）。"
        ),
        "setup": {
            "work.py": (
                "def decode_utf8(data):\n"
                "    return data.decode('utf-8')\n\n\n"
                "def encode_utf8(text):\n"
                "    return text.encode('utf-8')\n"
            ),
            "test_work.py": (
                "from work import decode_utf8\n\n\n"
                "def test_invalid_bytes():\n"
                "    assert decode_utf8(b'hello\\xffworld') == 'hello\\ufffdworld'\n"
            ),
        },
        "tests": {
            "test_f2p_decode.py": (
                "from work import decode_utf8\n\n\n"
                "def test_invalid_bytes():\n"
                "    assert decode_utf8(b'hello\\xffworld') == 'hello\\ufffdworld'\n\n\n"
                "def test_valid_utf8():\n"
                "    assert decode_utf8('你好'.encode('utf-8')) == '你好'\n\n\n"
                "def test_ascii():\n"
                "    assert decode_utf8(b'abc') == 'abc'\n"
            ),
            "test_p2p_encode.py": (
                "from work import encode_utf8\n\n\n"
                "def test_encode():\n"
                "    assert encode_utf8('ok') == b'ok'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    # =====================================================================
    # tool_recovery —— 导入/环境错误恢复（3 题）
    # =====================================================================
    {
        "id": "tool_recovery_001",
        "category": "tool_recovery",
        "kind": "code_change",
        "title": "解除三方循环导入",
        "difficulty": "hard",
        "prompt": (
            "import a 会报 ImportError（循环导入）：a → b → c → a 形成三方循环。\n"
            "请解除循环依赖，使三个模块都能被正常 import 和调用，且各自的函数行为不变。"
        ),
        "setup": {
            "a.py": (
                "from b import get_b\n\n\n"
                "def get_a():\n"
                "    return 'a' + get_b()\n"
            ),
            "b.py": (
                "from c import get_c\n\n\n"
                "def get_b():\n"
                "    return 'b' + get_c()\n"
            ),
            "c.py": (
                "from a import get_a\n\n\n"
                "def get_c():\n"
                "    return 'c'\n"
            ),
        },
        "tests": {
            "test_f2p_import.py": (
                "import a\n\n\n"
                "def test_get_a():\n"
                "    assert a.get_a() == 'abc'\n\n\n"
                "def test_modules_importable():\n"
                "    import b, c\n"
                "    assert b.get_b() == 'bc'\n"
                "    assert c.get_c() == 'c'\n"
            ),
            "test_p2p_c.py": (
                "import c\n\n\n"
                "def test_get_c():\n"
                "    assert c.get_c() == 'c'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "tool_recovery_002",
        "category": "tool_recovery",
        "kind": "code_change",
        "title": "修复包内相对导入错误",
        "difficulty": "hard",
        "prompt": (
            "运行 main.py 会报 ImportError：包 mypkg 内的模块用了错误的导入方式。\n"
            "请把包内模块之间的导入改为正确的相对导入，使 main.py 正常运行。"
        ),
        "setup": {
            "main.py": (
                "from mypkg.api import run\n\n\n"
                "def main():\n"
                "    return run()\n"
            ),
            "mypkg/__init__.py": "",
            "mypkg/api.py": (
                "from core import compute\n\n\n"
                "def run():\n"
                "    return compute(3)\n"
            ),
            "mypkg/core.py": (
                "def compute(n):\n"
                "    return n * 2\n"
            ),
        },
        "tests": {
            "test_f2p_import.py": (
                "import main\n\n\n"
                "def test_main():\n"
                "    assert main.main() == 6\n\n\n"
                "def test_core():\n"
                "    from mypkg.core import compute\n"
                "    assert compute(5) == 10\n"
            ),
            "test_p2p_core.py": (
                "from mypkg.core import compute\n\n\n"
                "def test_compute():\n"
                "    assert compute(7) == 14\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "tool_recovery_003",
        "category": "tool_recovery",
        "kind": "code_change",
        "title": "为弃用 API 提供兼容层",
        "difficulty": "hard",
        "prompt": (
            "旧代码用 legacy.get_value() 读取配置，但该模块已重命名为 config，"
            "且方法改名为 fetch()。运行 legacy 用户代码会报 ModuleNotFoundError。\n"
            "请提供一个兼容层（shim），让 from legacy import get_value 仍可用，"
            "内部委托给新的 config.fetch，且不破坏新模块。"
        ),
        "setup": {
            "config.py": (
                "def fetch(key):\n"
                "    return {'host': 'localhost', 'port': 8080}[key]\n"
            ),
            "legacy_user.py": (
                "from legacy import get_value\n\n\n"
                "def read_host():\n"
                "    return get_value('host')\n"
            ),
        },
        "tests": {
            "test_f2p_shim.py": (
                "from legacy_user import read_host\n\n\n"
                "def test_read_host():\n"
                "    assert read_host() == 'localhost'\n\n\n"
                "def test_shim_direct():\n"
                "    from legacy import get_value\n"
                "    assert get_value('port') == 8080\n"
            ),
            "test_p2p_config.py": (
                "from config import fetch\n\n\n"
                "def test_fetch():\n"
                "    assert fetch('host') == 'localhost'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    # =====================================================================
    # long_task —— 长任务（2 题，更复杂）
    # =====================================================================
    {
        "id": "long_task_001",
        "category": "long_task",
        "kind": "code_change",
        "title": "按设计文档实现多模块用户服务",
        "difficulty": "hard",
        "labels": ["long"],
        "timeout_seconds": 600,
        "prompt": (
            "这是一个需要分阶段完成的任务：\n"
            "1. 阅读 design.md，理解用户管理系统的设计与需求。\n"
            "2. 阅读 models.py、repo.py、validators.py，理解已有的数据模型、仓库和校验工具。\n"
            "3. 在 service.py 中实现 UserService 类（方法体目前是 raise NotImplementedError），"
            "组合已有模块完成业务逻辑。\n"
            "4. 自己验证实现的正确性（可直接运行测试或交互验证）。\n"
            "注意：不要改动 design.md、models.py、repo.py、validators.py。"
        ),
        "setup": {
            "design.md": (
                "# 用户管理系统设计\n\n"
                "UserService 负责用户的注册与查询，需求如下：\n\n"
                "- register(name, email)：注册用户，返回 user 字典（含 id/name/email）。\n"
                "  - name 去除首尾空白后非空，否则抛 ValueError。\n"
                "  - email 必须包含 @，否则抛 ValueError。\n"
                "  - email 已存在时抛 ValueError。\n"
                "  - id 从 1 开始自增。\n"
                "- find_by_email(email)：返回该 email 的用户，不存在返回 None。\n"
                "- count()：返回已注册用户总数。\n"
                "- all_names()：按注册顺序返回所有用户名列表。\n\n"
                "已有模块：models.make_user、repo.Repo、validators.is_valid_name / is_valid_email。\n"
            ),
            "models.py": (
                "def make_user(name, email, uid):\n"
                "    return {'id': uid, 'name': name, 'email': email}\n"
            ),
            "repo.py": (
                "class Repo:\n"
                "    def __init__(self):\n"
                "        self._users = []\n\n"
                "    def add(self, user):\n"
                "        self._users.append(user)\n\n"
                "    def find(self, email):\n"
                "        for u in self._users:\n"
                "            if u['email'] == email:\n"
                "                return u\n"
                "        return None\n\n"
                "    def all(self):\n"
                "        return list(self._users)\n"
            ),
            "validators.py": (
                "def is_valid_name(name):\n"
                "    return bool(name and name.strip())\n\n\n"
                "def is_valid_email(email):\n"
                "    return '@' in email\n"
            ),
            "service.py": (
                "from models import make_user\n"
                "from repo import Repo\n"
                "from validators import is_valid_name, is_valid_email\n\n\n"
                "class UserService:\n"
                "    def __init__(self):\n"
                "        self._repo = Repo()\n"
                "        self._next_id = 1\n\n"
                "    def register(self, name, email):\n"
                "        raise NotImplementedError\n\n"
                "    def find_by_email(self, email):\n"
                "        raise NotImplementedError\n\n"
                "    def count(self):\n"
                "        raise NotImplementedError\n\n"
                "    def all_names(self):\n"
                "        raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_service.py": (
                "import pytest\n"
                "from service import UserService\n\n\n"
                "def test_register_and_find():\n"
                "    s = UserService()\n"
                "    u = s.register('alice', 'alice@x.com')\n"
                "    assert s.find_by_email('alice@x.com') == u\n"
                "    assert s.count() == 1\n\n\n"
                "def test_duplicate_email():\n"
                "    s = UserService()\n"
                "    s.register('alice', 'a@x.com')\n"
                "    with pytest.raises(ValueError):\n"
                "        s.register('bob', 'a@x.com')\n\n\n"
                "def test_invalid_name():\n"
                "    s = UserService()\n"
                "    with pytest.raises(ValueError):\n"
                "        s.register('   ', 'a@x.com')\n\n\n"
                "def test_invalid_email():\n"
                "    s = UserService()\n"
                "    with pytest.raises(ValueError):\n"
                "        s.register('alice', 'nope')\n\n\n"
                "def test_all_names_order():\n"
                "    s = UserService()\n"
                "    s.register('alice', 'a@x.com')\n"
                "    s.register('bob', 'b@x.com')\n"
                "    assert s.all_names() == ['alice', 'bob']\n"
            ),
            "test_p2p_modules.py": (
                "from repo import Repo\n"
                "from validators import is_valid_email, is_valid_name\n\n\n"
                "def test_repo():\n"
                "    r = Repo()\n"
                "    r.add({'id': 1, 'email': 'a@x.com'})\n"
                "    assert r.find('a@x.com')['id'] == 1\n"
                "    assert r.find('none') is None\n\n\n"
                "def test_validators():\n"
                "    assert is_valid_name('x') is True\n"
                "    assert is_valid_name('  ') is False\n"
                "    assert is_valid_email('a@b') is True\n"
                "    assert is_valid_email('ab') is False\n"
            ),
        },
        "grader": {
            "type": "fail_to_pass",
            "forbidden_files": ["design.md", "models.py", "repo.py", "validators.py"],
        },
    },
    {
        "id": "long_task_002",
        "category": "long_task",
        "kind": "code_change",
        "title": "长流水线多环节错误定位与交叉校验",
        "difficulty": "hard",
        "labels": ["long"],
        "timeout_seconds": 600,
        "prompt": (
            "项目里有 20 个处理环节 stage_00.py 到 stage_19.py，pipeline.py 把它们串成流水线，\n"
            "cross.py 交叉引用 stage_00 和 stage_19 做聚合校验。\n"
            "每个 stage 的 transform(x) 应该返回 x + 该环节编号。\n"
            "但 pipeline 的最终输出不对，说明其中有不止一个环节实现有误。\n"
            "请在整个流水线中定位出所有错误环节并修复，使 pipeline.run(0) 和 cross.aggregate(0) 都返回正确结果。"
        ),
        "setup": _stages(20, bug=[5, 13], cross=True),
        "tests": {
            "test_f2p_pipeline.py": (
                "from pipeline import run\n\n\n"
                "def test_full_pipeline():\n"
                "    assert run(0) == 190\n\n\n"
                "def test_other_input():\n"
                "    assert run(5) == 195\n"
            ),
            "test_p2p_cross.py": (
                "from cross import aggregate\n\n\n"
                "def test_aggregate():\n"
                "    assert aggregate(0) == 19\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    # =====================================================================
    # algorithm —— 算法实现（4 题）
    # =====================================================================
    {
        "id": "algorithm_001",
        "category": "algorithm",
        "kind": "code_change",
        "title": "实现拓扑排序（课程依赖）",
        "difficulty": "hard",
        "prompt": (
            "toposort.py 里的 topological_sort 还没有实现。请实现它：\n"
            "输入是节点集合和边列表（每条边 (先修, 后修)），返回一个合法的拓扑序列表；\n"
            "若图中有环则抛 ValueError。不要改动已经实现的 build_graph 函数。"
        ),
        "setup": {
            "toposort.py": (
                "def build_graph(nodes, edges):\n"
                "    return {n: [] for n in nodes}\n\n\n"
                "def topological_sort(nodes, edges):\n"
                "    raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_topo.py": (
                "import pytest\n"
                "from toposort import topological_sort\n\n\n"
                "def test_chain():\n"
                "    order = topological_sort([1, 2, 3], [(1, 2), (2, 3)])\n"
                "    assert order.index(1) < order.index(2) < order.index(3)\n\n\n"
                "def test_diamond():\n"
                "    order = topological_sort(['a', 'b', 'c', 'd'], [('a', 'b'), ('a', 'c'), ('b', 'd'), ('c', 'd')])\n"
                "    assert order.index('a') < order.index('b')\n"
                "    assert order.index('a') < order.index('c')\n"
                "    assert order.index('b') < order.index('d')\n"
                "    assert order.index('c') < order.index('d')\n\n\n"
                "def test_cycle():\n"
                "    with pytest.raises(ValueError):\n"
                "        topological_sort([1, 2], [(1, 2), (2, 1)])\n"
            ),
            "test_p2p_graph.py": (
                "from toposort import build_graph\n\n\n"
                "def test_build():\n"
                "    assert build_graph([1], []) == {1: []}\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "algorithm_002",
        "category": "algorithm",
        "kind": "code_change",
        "title": "实现并查集（连通分量）",
        "difficulty": "hard",
        "prompt": (
            "unionfind.py 里的 UnionFind 还没有实现。请实现它：\n"
            "- union(a, b) 合并两个元素所在集合\n"
            "- find(a) 返回元素所在集合的代表元（带路径压缩）\n"
            "- connected(a, b) 判断两元素是否在同一集合\n"
            "不要改动已经实现的 count_nodes 函数。"
        ),
        "setup": {
            "unionfind.py": (
                "def count_nodes(n):\n"
                "    return n\n\n\n"
                "class UnionFind:\n"
                "    def __init__(self, n):\n"
                "        raise NotImplementedError\n\n"
                "    def find(self, x):\n"
                "        raise NotImplementedError\n\n"
                "    def union(self, a, b):\n"
                "        raise NotImplementedError\n\n"
                "    def connected(self, a, b):\n"
                "        raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_unionfind.py": (
                "from unionfind import UnionFind\n\n\n"
                "def test_union_connected():\n"
                "    uf = UnionFind(5)\n"
                "    uf.union(0, 1)\n"
                "    uf.union(1, 2)\n"
                "    assert uf.connected(0, 2) is True\n"
                "    assert uf.connected(0, 3) is False\n\n\n"
                "def test_transitive():\n"
                "    uf = UnionFind(6)\n"
                "    uf.union(0, 1)\n"
                "    uf.union(2, 3)\n"
                "    uf.union(1, 2)\n"
                "    assert uf.connected(0, 3) is True\n"
                "    assert uf.connected(4, 5) is False\n\n\n"
                "def test_self():\n"
                "    uf = UnionFind(3)\n"
                "    assert uf.connected(1, 1) is True\n"
            ),
            "test_p2p_count.py": (
                "from unionfind import count_nodes\n\n\n"
                "def test_count():\n"
                "    assert count_nodes(10) == 10\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "algorithm_003",
        "category": "algorithm",
        "kind": "code_change",
        "title": "实现最长无重复子串（滑动窗口）",
        "difficulty": "hard",
        "prompt": (
            "substring.py 里的 longest_unique_substring 还没有实现。请实现它：\n"
            "返回字符串中最长不含重复字符的子串长度。\n"
            "不要改动已经实现的 is_unique 函数。"
        ),
        "setup": {
            "substring.py": (
                "def is_unique(s):\n"
                "    return len(set(s)) == len(s)\n\n\n"
                "def longest_unique_substring(s):\n"
                "    raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_substring.py": (
                "from substring import longest_unique_substring\n\n\n"
                "def test_basic():\n"
                "    assert longest_unique_substring('abcabcbb') == 3\n\n\n"
                "def test_all_same():\n"
                "    assert longest_unique_substring('bbbbb') == 1\n\n\n"
                "def test_end():\n"
                "    assert longest_unique_substring('pwwkew') == 3\n\n\n"
                "def test_empty():\n"
                "    assert longest_unique_substring('') == 0\n\n\n"
                "def test_all_unique():\n"
                "    assert longest_unique_substring('abcdef') == 6\n"
            ),
            "test_p2p_unique.py": (
                "from substring import is_unique\n\n\n"
                "def test_is_unique():\n"
                "    assert is_unique('abc') is True\n"
                "    assert is_unique('aab') is False\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "algorithm_004",
        "category": "algorithm",
        "kind": "code_change",
        "title": "实现区间合并与插入",
        "difficulty": "hard",
        "prompt": (
            "intervals.py 里的 merge 和 insert 还没有实现。请实现它们：\n"
            "- merge(intervals) 合并所有重叠区间，返回按起点排序的不重叠区间列表\n"
            "- insert(intervals, new_interval) 把新区间插入并合并\n"
            "不要改动已经实现的 overlaps 函数。"
        ),
        "setup": {
            "intervals.py": (
                "def overlaps(a, b):\n"
                "    return a[0] <= b[1] and b[0] <= a[1]\n\n\n"
                "def merge(intervals):\n"
                "    raise NotImplementedError\n\n\n"
                "def insert(intervals, new_interval):\n"
                "    raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_intervals.py": (
                "from intervals import merge, insert\n\n\n"
                "def test_merge_overlapping():\n"
                "    assert merge([[1, 3], [2, 6], [8, 10], [15, 18]]) == [[1, 6], [8, 10], [15, 18]]\n\n\n"
                "def test_merge_touching():\n"
                "    assert merge([[1, 4], [4, 5]]) == [[1, 5]]\n\n\n"
                "def test_insert():\n"
                "    assert insert([[1, 3], [6, 9]], [2, 5]) == [[1, 5], [6, 9]]\n\n\n"
                "def test_insert_no_overlap():\n"
                "    assert insert([[1, 2], [5, 6]], [3, 4]) == [[1, 2], [3, 4], [5, 6]]\n"
            ),
            "test_p2p_overlaps.py": (
                "from intervals import overlaps\n\n\n"
                "def test_overlaps():\n"
                "    assert overlaps([1, 3], [2, 4]) is True\n"
                "    assert overlaps([1, 2], [3, 4]) is False\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    # =====================================================================
    # concurrency —— 并发（4 题）
    # =====================================================================
    {
        "id": "concurrency_001",
        "category": "concurrency",
        "kind": "code_change",
        "title": "实现线程安全计数器",
        "difficulty": "hard",
        "prompt": (
            "counter.py 里的 ThreadSafeCounter 还没有实现。请实现它：\n"
            "- increment() 每次加 1（线程安全）\n"
            "- value() 返回当前值\n"
            "要求：多线程并发调用 increment() 时结果必须精确无误。\n"
            "不要改动已经实现的 describe 函数。"
        ),
        "setup": {
            "counter.py": (
                "def describe():\n"
                "    return 'thread-safe counter'\n\n\n"
                "class ThreadSafeCounter:\n"
                "    def __init__(self):\n"
                "        raise NotImplementedError\n\n"
                "    def increment(self):\n"
                "        raise NotImplementedError\n\n"
                "    def value(self):\n"
                "        raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_counter.py": (
                "import threading\n"
                "from counter import ThreadSafeCounter\n\n\n"
                "def test_concurrent_increment():\n"
                "    c = ThreadSafeCounter()\n"
                "    n_threads, per = 8, 20000\n"
                "    def worker():\n"
                "        for _ in range(per):\n"
                "            c.increment()\n"
                "    threads = [threading.Thread(target=worker) for _ in range(n_threads)]\n"
                "    for t in threads:\n"
                "        t.start()\n"
                "    for t in threads:\n"
                "        t.join()\n"
                "    assert c.value() == n_threads * per\n\n\n"
                "def test_initial():\n"
                "    assert ThreadSafeCounter().value() == 0\n"
            ),
            "test_p2p_describe.py": (
                "from counter import describe\n\n\n"
                "def test_describe():\n"
                "    assert describe() == 'thread-safe counter'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "concurrency_002",
        "category": "concurrency",
        "kind": "code_change",
        "title": "实现线程安全键值存储",
        "difficulty": "hard",
        "prompt": (
            "kv.py 里的 ThreadSafeKV 还没有实现。请实现它：\n"
            "- get(key, default=None)\n"
            "- set(key, value)\n"
            "- delete(key)\n"
            "要求：多线程并发读写时不会丢失更新、不会崩溃。\n"
            "不要改动已经实现的 keys_of 函数。"
        ),
        "setup": {
            "kv.py": (
                "def keys_of(mapping):\n"
                "    return list(mapping.keys())\n\n\n"
                "class ThreadSafeKV:\n"
                "    def __init__(self):\n"
                "        raise NotImplementedError\n\n"
                "    def get(self, key, default=None):\n"
                "        raise NotImplementedError\n\n"
                "    def set(self, key, value):\n"
                "        raise NotImplementedError\n\n"
                "    def delete(self, key):\n"
                "        raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_kv.py": (
                "import threading\n"
                "from kv import ThreadSafeKV\n\n\n"
                "def test_concurrent_set():\n"
                "    kv = ThreadSafeKV()\n"
                "    def worker(i):\n"
                "        for j in range(1000):\n"
                "            kv.set(i, j)\n"
                "    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]\n"
                "    for t in threads:\n"
                "        t.start()\n"
                "    for t in threads:\n"
                "        t.join()\n"
                "    for i in range(10):\n"
                "        assert kv.get(i) == 999\n\n\n"
                "def test_basic_ops():\n"
                "    kv = ThreadSafeKV()\n"
                "    kv.set('a', 1)\n"
                "    assert kv.get('a') == 1\n"
                "    kv.delete('a')\n"
                "    assert kv.get('a', 'missing') == 'missing'\n"
            ),
            "test_p2p_keys.py": (
                "from kv import keys_of\n\n\n"
                "def test_keys():\n"
                "    assert keys_of({'a': 1, 'b': 2}) == ['a', 'b']\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "concurrency_003",
        "category": "concurrency",
        "kind": "code_change",
        "title": "实现 asyncio 并发聚合",
        "difficulty": "hard",
        "prompt": (
            "fetcher.py 里的 fetch_all 还没有实现。请实现它：\n"
            "并发调用 fetch(url) 获取所有 url 的结果，返回按输入顺序排列的结果列表。\n"
            "不要改动已经实现的 fetch 函数。"
        ),
        "setup": {
            "fetcher.py": (
                "import asyncio\n\n\n"
                "async def fetch(url):\n"
                "    await asyncio.sleep(0)\n"
                "    return f'{url}!'\n\n\n"
                "async def fetch_all(urls):\n"
                "    raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_fetch.py": (
                "import asyncio\n"
                "from fetcher import fetch_all\n\n\n"
                "def test_fetch_all():\n"
                "    result = asyncio.run(fetch_all(['a', 'b', 'c', 'd']))\n"
                "    assert result == ['a!', 'b!', 'c!', 'd!']\n\n\n"
                "def test_empty():\n"
                "    assert asyncio.run(fetch_all([])) == []\n"
            ),
            "test_p2p_fetch_one.py": (
                "import asyncio\n"
                "from fetcher import fetch\n\n\n"
                "def test_fetch_one():\n"
                "    assert asyncio.run(fetch('x')) == 'x!'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "concurrency_004",
        "category": "concurrency",
        "kind": "code_change",
        "title": "实现生产者消费者队列",
        "difficulty": "hard",
        "prompt": (
            "pipeline.py 里的 BoundedQueue 还没有实现。请实现它（基于标准库 queue.Queue）：\n"
            "- put(item) 放入一个元素\n"
            "- get() 取出一个元素（阻塞直到有元素）\n"
            "- close() 标记生产结束（向队列放入结束哨兵 Sentinel）\n"
            "- 支持迭代（__iter__），迭代在遇到结束哨兵时停止\n"
            "要求：一个生产者线程、一个消费者线程能通过 for 迭代正确协作，消费到全部元素。\n"
            "不要改动已经实现的 Sentinel 常量。"
        ),
        "setup": {
            "pipeline.py": (
                "Sentinel = object()\n\n\n"
                "class BoundedQueue:\n"
                "    def __init__(self, maxsize=100):\n"
                "        raise NotImplementedError\n\n"
                "    def put(self, item):\n"
                "        raise NotImplementedError\n\n"
                "    def get(self):\n"
                "        raise NotImplementedError\n\n"
                "    def close(self):\n"
                "        raise NotImplementedError\n\n"
                "    def __iter__(self):\n"
                "        raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_pipeline.py": (
                "import threading\n"
                "from pipeline import BoundedQueue\n\n\n"
                "def test_producer_consumer():\n"
                "    q = BoundedQueue()\n"
                "    consumed = []\n"
                "    def producer():\n"
                "        for i in range(100):\n"
                "            q.put(i)\n"
                "        q.close()\n"
                "    def consumer():\n"
                "        for item in q:\n"
                "            consumed.append(item)\n"
                "    t1 = threading.Thread(target=producer)\n"
                "    t2 = threading.Thread(target=consumer)\n"
                "    t1.start()\n"
                "    t2.start()\n"
                "    t1.join()\n"
                "    t2.join()\n"
                "    assert consumed == list(range(100))\n"
            ),
            "test_p2p_sentinel.py": (
                "from pipeline import Sentinel\n\n\n"
                "def test_sentinel():\n"
                "    assert Sentinel is not None\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    # =====================================================================
    # parsing —— 解析/状态机（4 题）
    # =====================================================================
    {
        "id": "parsing_001",
        "category": "parsing",
        "kind": "code_change",
        "title": "实现四则运算表达式求值",
        "difficulty": "hard",
        "prompt": (
            "evaluator.py 里的 evaluate 还没有实现。请实现它：\n"
            "解析并计算只含整数、加减乘除和括号的表达式字符串，返回整数值。\n"
            "注意运算符优先级和括号。不要改动已经实现的 tokenize 函数。"
        ),
        "setup": {
            "evaluator.py": (
                "def tokenize(expr):\n"
                "    return list(expr.replace(' ', ''))\n\n\n"
                "def evaluate(expr):\n"
                "    raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_eval.py": (
                "from evaluator import evaluate\n\n\n"
                "def test_basic():\n"
                "    assert evaluate('1+2') == 3\n\n\n"
                "def test_precedence():\n"
                "    assert evaluate('2+3*4') == 14\n\n\n"
                "def test_parens():\n"
                "    assert evaluate('(2+3)*4') == 20\n\n\n"
                "def test_division():\n"
                "    assert evaluate('7-3') == 4\n"
                "    assert evaluate('8/2') == 4\n\n\n"
                "def test_nested():\n"
                "    assert evaluate('2*(3+(4-1))') == 12\n"
            ),
            "test_p2p_tokenize.py": (
                "from evaluator import tokenize\n\n\n"
                "def test_tokenize():\n"
                "    assert tokenize('1 + 2') == ['1', '+', '2']\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "parsing_002",
        "category": "parsing",
        "kind": "code_change",
        "title": "实现 INI 配置解析器",
        "difficulty": "hard",
        "prompt": (
            "iniparse.py 里的 parse_ini 还没有实现。请实现它：\n"
            "解析 INI 文本，返回 {section: {key: value}} 的嵌套字典。\n"
            "规则：以 [section] 开头的行定义节；key = value 定义键值；\n"
            "忽略空行和以 # 或 ; 开头的注释行；节名/键值去首尾空白。\n"
            "不要改动已经实现的 strip_comment 函数。"
        ),
        "setup": {
            "iniparse.py": (
                "def strip_comment(line):\n"
                "    return line.split('#')[0].split(';')[0].strip()\n\n\n"
                "def parse_ini(text):\n"
                "    raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_ini.py": (
                "from iniparse import parse_ini\n\n\n"
                "def test_sections():\n"
                "    text = '[db]\\nhost = localhost\\nport = 5432\\n\\n[app]\\ndebug = true\\n'\n"
                "    assert parse_ini(text) == {'db': {'host': 'localhost', 'port': '5432'}, 'app': {'debug': 'true'}}\n\n\n"
                "def test_comments():\n"
                "    text = '[s]\\n# comment\\n; another\\nkey = value\\n'\n"
                "    assert parse_ini(text) == {'s': {'key': 'value'}}\n\n\n"
                "def test_empty():\n"
                "    assert parse_ini('') == {}\n"
            ),
            "test_p2p_strip.py": (
                "from iniparse import strip_comment\n\n\n"
                "def test_strip():\n"
                "    assert strip_comment('  key = value  # note') == 'key = value'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "parsing_003",
        "category": "parsing",
        "kind": "code_change",
        "title": "实现有限状态机",
        "difficulty": "hard",
        "prompt": (
            "fsm.py 里的 StateMachine 还没有实现。请实现它：\n"
            "- 用状态转移表驱动，transitions 是 {state: {event: next_state}}\n"
            "- trigger(event) 根据当前状态转移，返回新状态；非法转移抛 ValueError\n"
            "- current 属性返回当前状态\n"
            "不要改动已经实现的 make_transitions 函数。"
        ),
        "setup": {
            "fsm.py": (
                "def make_transitions():\n"
                "    return {'idle': {'start': 'running'}, 'running': {'stop': 'idle', 'pause': 'paused'}, 'paused': {'resume': 'running'}}\n\n\n"
                "class StateMachine:\n"
                "    def __init__(self, initial, transitions):\n"
                "        raise NotImplementedError\n\n"
                "    def trigger(self, event):\n"
                "        raise NotImplementedError\n\n"
                "    @property\n"
                "    def current(self):\n"
                "        raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_fsm.py": (
                "import pytest\n"
                "from fsm import StateMachine, make_transitions\n\n\n"
                "def test_transitions():\n"
                "    sm = StateMachine('idle', make_transitions())\n"
                "    assert sm.current == 'idle'\n"
                "    assert sm.trigger('start') == 'running'\n"
                "    assert sm.trigger('pause') == 'paused'\n"
                "    assert sm.trigger('resume') == 'running'\n"
                "    assert sm.trigger('stop') == 'idle'\n\n\n"
                "def test_illegal():\n"
                "    sm = StateMachine('idle', make_transitions())\n"
                "    with pytest.raises(ValueError):\n"
                "        sm.trigger('stop')\n"
            ),
            "test_p2p_transitions.py": (
                "from fsm import make_transitions\n\n\n"
                "def test_make():\n"
                "    t = make_transitions()\n"
                "    assert 'idle' in t and 'running' in t\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "parsing_004",
        "category": "parsing",
        "kind": "code_change",
        "title": "实现 CSV 完整解析",
        "difficulty": "hard",
        "prompt": (
            "csvparse.py 里的 parse_csv 还没有实现。请实现它：\n"
            "把单行 CSV 文本解析成字段列表。规则：\n"
            "- 用逗号分隔字段\n"
            "- 用双引号包裹的字段可以包含逗号，内部连续两个双引号表示一个双引号\n"
            "不要改动已经实现的 split_plain 函数。"
        ),
        "setup": {
            "csvparse.py": (
                "def split_plain(line):\n"
                "    return line.split(',')\n\n\n"
                "def parse_csv(line):\n"
                "    raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_csv.py": (
                "from csvparse import parse_csv\n\n\n"
                "def test_plain():\n"
                "    assert parse_csv('a,b,c') == ['a', 'b', 'c']\n\n\n"
                "def test_quoted_comma():\n"
                "    assert parse_csv('a,\"b,c\",d') == ['a', 'b,c', 'd']\n\n\n"
                "def test_escaped_quote():\n"
                "    assert parse_csv('\"say \"\"hi\"\"\",x') == ['say \"hi\"', 'x']\n\n\n"
                "def test_empty_fields():\n"
                "    assert parse_csv('a,,c') == ['a', '', 'c']\n"
            ),
            "test_p2p_split.py": (
                "from csvparse import split_plain\n\n\n"
                "def test_split():\n"
                "    assert split_plain('x,y') == ['x', 'y']\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    # =====================================================================
    # optimization —— 性能（4 题，大数据量约束复杂度）
    # =====================================================================
    {
        "id": "optimization_001",
        "category": "optimization",
        "kind": "code_change",
        "title": "实现 O(n) 两数之和",
        "difficulty": "hard",
        "prompt": (
            "twosum.py 里的 two_sum 还没有实现。请实现它：\n"
            "在 nums 里找两个下标不同的数，使它们的和等于 target，返回这两个数的下标（任意顺序）。\n"
            "要求 O(n) 时间。输入规模可达数万，请勿用 O(n²) 暴力。\n"
            "不要改动已经实现的 pair_sum 函数。"
        ),
        "setup": {
            "twosum.py": (
                "def pair_sum(nums, target):\n"
                "    return None\n\n\n"
                "def two_sum(nums, target):\n"
                "    raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_twosum.py": (
                "from twosum import two_sum\n\n\n"
                "def test_small():\n"
                "    i, j = two_sum([2, 7, 11, 15], 9)\n"
                "    assert sorted([i, j]) == [0, 1]\n\n\n"
                "def test_large():\n"
                "    n = 30000\n"
                "    nums = list(range(n))\n"
                "    i, j = two_sum(nums, n - 2 + n - 1)\n"
                "    assert sorted([i, j]) == [n - 2, n - 1]\n\n\n"
                "def test_duplicates():\n"
                "    i, j = two_sum([3, 3], 6)\n"
                "    assert sorted([i, j]) == [0, 1]\n"
            ),
            "test_p2p_pair.py": (
                "from twosum import pair_sum\n\n\n"
                "def test_pair_sum():\n"
                "    assert pair_sum([1, 2], 3) is None\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "optimization_002",
        "category": "optimization",
        "kind": "code_change",
        "title": "实现前缀和 O(1) 区间查询",
        "difficulty": "hard",
        "prompt": (
            "prefixsum.py 里的 PrefixSum 还没有实现。请实现它：\n"
            "- __init__(nums) 预处理\n"
            "- range_sum(i, j) 返回 nums[i..j]（含两端）之和，要求 O(1) 查询\n"
            "不要改动已经实现的 brute_sum 函数。"
        ),
        "setup": {
            "prefixsum.py": (
                "def brute_sum(nums, i, j):\n"
                "    return sum(nums[i:j + 1])\n\n\n"
                "class PrefixSum:\n"
                "    def __init__(self, nums):\n"
                "        raise NotImplementedError\n\n"
                "    def range_sum(self, i, j):\n"
                "        raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_prefix.py": (
                "from prefixsum import PrefixSum\n\n\n"
                "def test_basic():\n"
                "    ps = PrefixSum([1, 2, 3, 4, 5])\n"
                "    assert ps.range_sum(1, 3) == 9\n"
                "    assert ps.range_sum(0, 4) == 15\n\n\n"
                "def test_large_many_queries():\n"
                "    n = 30000\n"
                "    nums = list(range(1, n + 1))\n"
                "    ps = PrefixSum(nums)\n"
                "    for _ in range(10000):\n"
                "        assert ps.range_sum(0, n - 1) == n * (n + 1) // 2\n"
            ),
            "test_p2p_brute.py": (
                "from prefixsum import brute_sum\n\n\n"
                "def test_brute():\n"
                "    assert brute_sum([1, 2, 3], 0, 1) == 3\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "optimization_003",
        "category": "optimization",
        "kind": "code_change",
        "title": "实现迭代版斐波那契",
        "difficulty": "hard",
        "prompt": (
            "fib.py 里的 fib 还没有实现。请实现它：返回第 n 个斐波那契数（0 起）。\n"
            "要求 O(n) 时间、O(1) 空间，n 可达数万，勿用指数级递归。\n"
            "不要改动已经实现的 fib_small 函数。"
        ),
        "setup": {
            "fib.py": (
                "def fib_small(n):\n"
                "    a, b = 0, 1\n"
                "    for _ in range(n):\n"
                "        a, b = b, a + b\n"
                "    return a\n\n\n"
                "def fib(n):\n"
                "    raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_fib.py": (
                "from fib import fib\n\n\n"
                "def test_small():\n"
                "    assert fib(0) == 0\n"
                "    assert fib(1) == 1\n"
                "    assert fib(10) == 55\n\n\n"
                "def test_large():\n"
                "    # 用迭代自测大 n，要求秒级返回\n"
                "    n = 200000\n"
                "    a, b = 0, 1\n"
                "    for _ in range(n):\n"
                "        a, b = b, a + b\n"
                "    assert fib(n) == a\n"
            ),
            "test_p2p_small.py": (
                "from fib import fib_small\n\n\n"
                "def test_fib_small():\n"
                "    assert fib_small(5) == 5\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "optimization_004",
        "category": "optimization",
        "kind": "code_change",
        "title": "实现 O(n) 有序去重",
        "difficulty": "hard",
        "prompt": (
            "dedupe.py 里的 dedupe 还没有实现。请实现它：\n"
            "去除列表中的重复元素，保持首次出现的顺序，返回新列表。\n"
            "要求 O(n) 时间。输入规模可达数万，请勿用 O(n²) 的 in 判断。\n"
            "不要改动已经实现的 first_occurrence 函数。"
        ),
        "setup": {
            "dedupe.py": (
                "def first_occurrence(items, target):\n"
                "    return items.index(target)\n\n\n"
                "def dedupe(items):\n"
                "    raise NotImplementedError\n"
            ),
        },
        "tests": {
            "test_f2p_dedupe.py": (
                "from dedupe import dedupe\n\n\n"
                "def test_small():\n"
                "    assert dedupe([1, 2, 2, 3, 1]) == [1, 2, 3]\n\n\n"
                "def test_order():\n"
                "    assert dedupe(['b', 'a', 'b', 'c', 'a']) == ['b', 'a', 'c']\n\n\n"
                "def test_large():\n"
                "    n = 30000\n"
                "    items = [i % 1000 for i in range(n)]\n"
                "    assert dedupe(items) == list(range(1000))\n"
            ),
            "test_p2p_first.py": (
                "from dedupe import first_occurrence\n\n\n"
                "def test_first():\n"
                "    assert first_occurrence([5, 6, 5], 5) == 0\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    # =====================================================================
    # permission —— 权限规则约束下完成任务（2 题）
    # 通过 setup 里的 .kyncode/permissions.yaml 注入 deny 规则，测 agent 在
    # 写/读被禁时能否换路径完成任务。判分 = pytest 通过 + forbidden_files 约束。
    # =====================================================================
    {
        "id": "permission_001",
        "category": "permission",
        "kind": "code_change",
        "title": "受保护文件不可改，改实现方适配",
        "difficulty": "hard",
        "prompt": (
            "calc.py 的 total 函数硬编码了税率 1.2，正确做法是使用 rate.py 里的 TAX_RATE。\n"
            "注意：rate.py 是受保护文件，权限规则禁止修改它。请只修改 calc.py，"
            "让 total 使用 TAX_RATE（TAX_RATE = 0.1）计算。"
        ),
        "setup": {
            "rate.py": "TAX_RATE = 0.1\n",
            "calc.py": (
                "from rate import TAX_RATE\n\n\n"
                "def total(price):\n"
                "    return price * 1.2\n"
            ),
            ".kyncode/permissions.yaml": (
                "- rule: \"WriteFile(rate.py)\"\n"
                "  effect: deny\n"
                "- rule: \"EditFile(rate.py)\"\n"
                "  effect: deny\n"
            ),
        },
        "tests": {
            "test_f2p_rate.py": (
                "from calc import total\n\n\n"
                "def test_total_uses_tax_rate():\n"
                "    assert abs(total(100) - 110) < 1e-9\n\n\n"
                "def test_total_zero():\n"
                "    assert total(0) == 0\n"
            ),
        },
        "grader": {"type": "fail_to_pass", "forbidden_files": ["rate.py"]},
    },
    {
        "id": "permission_002",
        "category": "permission",
        "kind": "code_change",
        "title": "敏感文件不可读，凭线索修复导入名",
        "difficulty": "hard",
        "prompt": (
            "app.py 从 config.py 导入了 SECRET_KEY，但 config.py 里导出的名字其实是 API_KEY。\n"
            "config.py 内容敏感，权限规则禁止读取它。请不要尝试读 config.py，"
            "直接根据这个线索修复 app.py 的导入和引用。"
        ),
        "setup": {
            "config.py": "API_KEY = 'sk-secret-123'\n",
            "app.py": (
                "from config import SECRET_KEY\n\n\n"
                "def auth():\n"
                "    return SECRET_KEY\n"
            ),
            ".kyncode/permissions.yaml": (
                "- rule: \"ReadFile(config.py)\"\n"
                "  effect: deny\n"
                "- rule: \"Grep(config.py)\"\n"
                "  effect: deny\n"
            ),
        },
        "tests": {
            "test_f2p_auth.py": (
                "from app import auth\n\n\n"
                "def test_auth_returns_api_key():\n"
                "    assert auth() == 'sk-secret-123'\n"
            ),
        },
        "grader": {"type": "fail_to_pass", "forbidden_files": ["config.py"]},
    },
    # =====================================================================
    # hook —— 事件钩子约束下完成任务（2 题）
    # 通过 setup 里的 config.yaml 注入 pre_tool_use reject 钩子，测 agent 在
    # 某类工具被钩子拒绝时能否改用其他工具完成任务。判分 = pytest 通过。
    # =====================================================================
    {
        "id": "hook_001",
        "category": "hook",
        "kind": "code_change",
        "title": "钩子禁用 Bash，改用文件工具修复",
        "difficulty": "hard",
        "prompt": (
            "sum.py 的 total 函数有 bug。\n"
            "注意：环境配置了钩子，禁止使用 Bash 工具。请只用文件读写工具（Read/Edit/Write）"
            "修复，不要尝试运行任何命令。"
        ),
        "setup": {
            "sum.py": (
                "def total(nums):\n"
                "    return sum(nums) - 1\n"
            ),
            "config.yaml": (
                "hooks:\n"
                "  - id: block_bash\n"
                "    event: pre_tool_use\n"
                "    action:\n"
                "      type: prompt\n"
                "      message: \"Bash tool is disabled for this task\"\n"
                "    if: tool == \"Bash\"\n"
                "    reject: true\n"
            ),
        },
        "tests": {
            "test_f2p_sum.py": (
                "from sum import total\n\n\n"
                "def test_total():\n"
                "    assert total([1, 2, 3]) == 6\n\n\n"
                "def test_empty():\n"
                "    assert total([]) == 0\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
    {
        "id": "hook_002",
        "category": "hook",
        "kind": "code_change",
        "title": "钩子禁用 WriteFile，改用 EditFile 修复",
        "difficulty": "hard",
        "prompt": (
            "text.py 的 greet 函数返回了 'hi'，正确应返回 'hello'。\n"
            "注意：环境配置了钩子，禁止使用 WriteFile 工具新建/覆盖文件。"
            "请用 EditFile 原地修改 text.py。"
        ),
        "setup": {
            "text.py": (
                "def greet():\n"
                "    return 'hi'\n"
            ),
            "config.yaml": (
                "hooks:\n"
                "  - id: block_write\n"
                "    event: pre_tool_use\n"
                "    action:\n"
                "      type: prompt\n"
                "      message: \"WriteFile tool is disabled for this task\"\n"
                "    if: tool == \"WriteFile\"\n"
                "    reject: true\n"
            ),
        },
        "tests": {
            "test_f2p_greet.py": (
                "from text import greet\n\n\n"
                "def test_greet():\n"
                "    assert greet() == 'hello'\n"
            ),
        },
        "grader": {"type": "fail_to_pass"},
    },
]


def _write_task(t: dict[str, Any]) -> None:
    """写 tasks/<id>.yaml 与 datasets/<category>/<id>/{setup,tests}/。"""
    task_id = t["id"]
    category = t["category"]

    yaml_body = {
        "id": task_id,
        "category": category,
        "kind": t["kind"],
        "title": t["title"],
        "prompt": t["prompt"],
        "timeout_seconds": t.get("timeout_seconds", 300),
        "max_retries": t.get("max_retries", 0),
        "difficulty": t.get("difficulty", "hard"),
        "labels": t.get("labels", []),
        "grader": t["grader"],
    }
    task_file = TASKS_DIR / f"{task_id}.yaml"
    task_file.parent.mkdir(parents=True, exist_ok=True)
    task_file.write_text(
        yaml.safe_dump(yaml_body, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

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
