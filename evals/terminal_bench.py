"""Terminal-Bench 接入：用 Docker 容器跑 kyncode 解 Terminal-Bench 任务，复用 evals 报告链路。

架构（复用现有 adapter + 报告链路）：
- 数据层：解析 Terminal-Bench 任务目录（task.yaml），产出 ``TerminalTask``。
- 环境层：docker compose 起任务容器（支持多服务任务，如依赖 decryptor/数据库的任务）
           → 装 kyncode（wheel）→ 注入精简 config。
- 执行层：容器内 ``kyncode -p "<instruction>" --output-format stream-json``，
          复用 ``adapter._parse_ndjson`` 解析（与本地 CLIAgentAdapter 完全一致的 headless 协议）。
- 判分层：容器内跑 run-tests.sh（pytest），复现官方 PytestParser 的
          「short test summary info 段解析 PASSED/FAILED」逻辑，resolved = 全部通过。
- 报告层：结果适配成 ``TaskOutcome``，复用 ``metrics.compute_metrics`` + ``report.render_report``。

前置：Docker 已启动；基础镜像已通过 ghcr 加速器拉取并 tag 成 ghcr.io 原名
（见 README 的 Terminal-Bench 接入说明）。
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from adapter import _parse_ndjson, parse_trace
from tasklib import Attempt, GraderResult, TaskOutcome, TaskSpec

# Terminal-Bench 官方的数据集版本锚点（对应 leaderboard 口径）
DATASET_NAME = "terminal-bench-core"
DATASET_VERSION = "0.1.1"

# 判分状态映射：官方 PytestTestStatus.to_test_status()
_PASSING = {"PASSED", "XFAIL", "SKIPPED"}
_FAILING = {"FAILED", "XPASS", "ERROR"}


# ---------------------------------------------------------------------------
# 数据层
# ---------------------------------------------------------------------------


@dataclass
class TerminalTask:
    """从 Terminal-Bench 任务目录解析出的任务定义。"""

    id: str
    category: str
    difficulty: str
    instruction: str
    path: Path
    agent_timeout_sec: float
    test_timeout_sec: float

    @property
    def title(self) -> str:
        return self.id

    @classmethod
    def load(cls, task_dir: Path) -> "TerminalTask":
        task_yaml = task_dir / "task.yaml"
        raw = yaml.safe_load(task_yaml.read_text(encoding="utf-8")) or {}
        return cls(
            id=task_dir.name,
            category=str(raw.get("category", "unknown")),
            difficulty=str(raw.get("difficulty", "")),
            instruction=str(raw.get("instruction", "")).strip(),
            path=task_dir,
            agent_timeout_sec=float(raw.get("max_agent_timeout_sec", 900)),
            test_timeout_sec=float(raw.get("max_test_timeout_sec", 180)),
        )


def load_terminal_tasks(
    tasks_root: Path,
    task_ids: list[str] | None = None,
    category: str | None = None,
    difficulty: str | None = None,
    n: int | None = None,
    exclude: list[str] | None = None,
) -> list[TerminalTask]:
    """加载 Terminal-Bench 任务目录里的任务，支持按 id / category / difficulty / 数量过滤。"""
    tasks: list[TerminalTask] = []
    for task_dir in sorted(tasks_root.iterdir()):
        if not task_dir.is_dir():
            continue
        if not (task_dir / "task.yaml").exists():
            continue
        t = TerminalTask.load(task_dir)
        if task_ids and t.id not in task_ids:
            continue
        if category and t.category != category:
            continue
        if difficulty and t.difficulty != difficulty:
            continue
        if exclude and t.id in exclude:
            continue
        tasks.append(t)
    if n is not None:
        tasks = tasks[:n]
    return tasks


# ---------------------------------------------------------------------------
# 判分（复现官方 PytestParser 逻辑）
# ---------------------------------------------------------------------------


def parse_pytest(content: str) -> tuple[dict[str, str], bool]:
    """解析 pytest 输出的 ``short test summary info`` 段。

    返回 ``(test_name -> "passed"|"failed", 是否成功解析)``。
    与官方 ``PytestParser.parse`` 一致：找不到 summary 段视为解析失败。
    """
    parts = re.split(
        r"=+\s*short test summary info\s*=+",
        content,
        maxsplit=1,
        flags=re.IGNORECASE,
    )
    if len(parts) < 2:
        return {}, False

    results: dict[str, str] = {}
    for raw_line in parts[1].splitlines():
        line = raw_line.strip()
        status = next((s for s in _PASSING | _FAILING if line.startswith(s)), None)
        if status is None:
            continue
        # FAILED 行末尾可能带 " - 描述"，去掉描述只留测试标识（官方 _clean_line）
        if status == "FAILED":
            seg = line.split(" - ")
            if len(seg) > 1:
                line = " - ".join(seg[:-1])
        rest = line[len(status):].strip()
        test_name = rest.split("::", 1)[-1].strip() if "::" in rest else rest
        if not test_name:
            continue
        results[test_name] = "passed" if status in _PASSING else "failed"
    return results, True


# ---------------------------------------------------------------------------
# 执行器
# ---------------------------------------------------------------------------


@dataclass
class TerminalBenchRunner:
    """单个 Terminal-Bench 任务的容器化执行：build → run → install → agent → grade。"""

    tasks_root: Path
    wheel_path: Path
    agent_config_path: Path
    logs_root: Path | None = None  # compose 挂载日志的宿主目录
    no_rebuild: bool = False
    cleanup: bool = False  # 跑完是否删除镜像（默认保留以复用 build cache）

    def __init__(
        self,
        tasks_root: Path,
        wheel_path: Path,
        agent_config_path: Path,
        logs_root: Path | None = None,
        no_rebuild: bool = False,
        cleanup: bool = False,
    ):
        self.tasks_root = tasks_root
        self.wheel_path = wheel_path
        self.agent_config_path = agent_config_path
        self.logs_root = logs_root
        self.no_rebuild = no_rebuild
        self.cleanup = cleanup
        # 构建期预装 kyncode 的基础镜像缓存（base_image -> tag）
        self._base_cache: set[str] = set()
        # 串行化 base 镜像构建，避免并发 build 同一 tag 冲突
        self._base_lock = threading.Lock()

    # ---- docker 封装 ------------------------------------------------------

    def _docker(self, *args: str, timeout: int | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", *args], capture_output=True, text=True, timeout=timeout
        )

    def _image_name(self, task_id: str) -> str:
        return f"tb-eval-{task_id}"

    # ---- 环境层（docker compose，支持多服务任务） -------------------------

    def _compose_env(self, task: TerminalTask, client_container: str, logs_root: Path) -> dict[str, str]:
        """复现官方 DockerComposeManager 的 env 注入（T_BENCH_ 前缀）。"""
        return {
            "T_BENCH_TASK_DOCKER_CLIENT_IMAGE_NAME": self._image_name(task.id),
            "T_BENCH_TASK_DOCKER_CLIENT_CONTAINER_NAME": client_container,
            "T_BENCH_TEST_DIR": "/tests",
            "T_BENCH_TASK_LOGS_PATH": str(logs_root / "sessions"),
            "T_BENCH_CONTAINER_LOGS_PATH": "/logs",
            "T_BENCH_TASK_AGENT_LOGS_PATH": str(logs_root / "agent-logs"),
            "T_BENCH_CONTAINER_AGENT_LOGS_PATH": "/agent-logs",
        }

    def _client_build_spec(self, task: TerminalTask) -> tuple[Path, Path] | None:
        """解析 compose，返回 client 服务的 (dockerfile 绝对路径, build context 绝对路径)。

        仅当 compose 只有一个 build 服务（= client）时返回，否则 None（fallback 运行时装）。
        """
        compose_file = task.path / "docker-compose.yaml"
        if not compose_file.exists():
            return None
        try:
            raw = yaml.safe_load(compose_file.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return None
        services = raw.get("services", {})
        if not isinstance(services, dict):
            return None
        build_services = [
            k for k, v in services.items()
            if isinstance(v, dict) and "build" in v
        ]
        if len(build_services) != 1:
            return None  # 多 build 服务（如 simple-sheets-put 的 api）fallback
        build = services[build_services[0]].get("build")
        if isinstance(build, str):
            ctx = Path(build)
            dockerfile_name = "Dockerfile"
        elif isinstance(build, dict):
            ctx = Path(build.get("context", "."))
            dockerfile_name = str(build.get("dockerfile", "Dockerfile"))
        else:
            return None
        context_abs = (task.path / ctx).resolve()
        dockerfile_abs = (context_abs / dockerfile_name).resolve()
        if not dockerfile_abs.exists():
            return None
        return dockerfile_abs, context_abs

    def _extract_base_image(self, dockerfile_path: Path) -> str | None:
        """读 Dockerfile，判断能否构建期预装，返回干净的基础镜像名（否则 None）。

        预装条件：单 FROM、外部镜像（非内部 stage 引用）、无 --platform、无 ARG 变量。
        """
        try:
            lines = dockerfile_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return None
        from_lines = [ln for ln in lines if ln.strip().upper().startswith("FROM")]
        if len(from_lines) != 1:
            return None  # 多阶段 Dockerfile 不预装（fallback 运行时装）
        tokens = from_lines[0].split()
        if len(tokens) < 2:
            return None
        if tokens[1].startswith("--platform"):
            return None  # amd64 特殊任务 fallback
        image = tokens[1]
        if "@" in image:
            image = image.split("@")[0]
        if "$" in image or "{" in image:
            return None  # ARG 变量 fallback
        return image

    def _build_kyncode_base(self, base_image: str) -> str:
        """构建「基础镜像 + kyncode」镜像，返回 tag（进程内 + 本地镜像缓存）。

        在任务 Dockerfile 的其余指令（如 broken-python 的 ``rm -rf pip``）执行前
        装好 kyncode，运行时无需 pip，对齐官方 AbstractInstalledAgent 的构建期预装。
        """
        tag = "tb-kyncode-base:" + hashlib.sha256(base_image.encode()).hexdigest()[:16]
        if tag in self._base_cache:
            return tag
        if self._docker("image", "inspect", tag).returncode == 0:
            self._base_cache.add(tag)
            return tag
        with self._base_lock:
            # 双重检查：等锁期间可能已被其他线程构建完成
            if tag in self._base_cache:
                return tag
            if self._docker("image", "inspect", tag).returncode == 0:
                self._base_cache.add(tag)
                return tag
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)
                wheel_name = self.wheel_path.name
                shutil.copy2(self.wheel_path, tmp_dir / wheel_name)
                (tmp_dir / "Dockerfile").write_text(
                    f"FROM {base_image}\n"
                    f"COPY {wheel_name} /tmp/{wheel_name}\n"
                    f"RUN python3 -m pip install -q --break-system-packages /tmp/{wheel_name}\n",
                    encoding="utf-8",
                )
                self._build_with_retry(tmp_dir, tag, f"kyncode-base({base_image})")
            self._base_cache.add(tag)
        return tag

    def _make_wrapper_dockerfile(self, dockerfile_path: Path, base_tag: str) -> str:
        """原 Dockerfile 的 FROM 行替换成预装镜像，写到临时文件并返回其路径。"""
        out: list[str] = []
        for line in dockerfile_path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^(\s*FROM\s+)(\S+)(\s+AS\s+\S+\s*)?$", line, re.IGNORECASE)
            if m:
                out.append(f"{m.group(1)}{base_tag}{m.group(3) or ''}")
            else:
                out.append(line)
        with tempfile.NamedTemporaryFile(
            "w", suffix=".dockerfile", delete=False, encoding="utf-8"
        ) as f:
            f.write("\n".join(out) + "\n")
            return f.name

    def _build_with_retry(
        self,
        context: Path,
        tag: str,
        label: str,
        dockerfile: str | None = None,
        attempts: int = 2,
    ) -> None:
        """``docker build --network=host``，失败重试（规避 compose bridge 网络下 apt 源偶发抖动）。"""
        last: subprocess.CompletedProcess | None = None
        for i in range(attempts):
            cmd = ["docker", "build", "--network=host", "-t", tag]
            if dockerfile:
                cmd += ["-f", str(dockerfile)]
            cmd.append(str(context))
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            if r.returncode == 0:
                return
            last = r
            if i < attempts - 1:
                time.sleep(3)
        assert last is not None
        raise RuntimeError(f"构建失败 {label}:\n{(last.stderr or last.stdout)[-2000:]}")

    def _build_client_image(
        self,
        task: TerminalTask,
        dockerfile_path: Path,
        context_path: Path,
        image_name: str,
        base_image: str,
    ) -> None:
        """构建 client 镜像：wrapper Dockerfile（FROM → 预装 kyncode 的镜像）+ host 网络 build。"""
        base_tag = self._build_kyncode_base(base_image)
        wrapper = self._make_wrapper_dockerfile(dockerfile_path, base_tag)
        try:
            self._build_with_retry(
                context_path, image_name, task.id, dockerfile=str(wrapper)
            )
        finally:
            Path(wrapper).unlink(missing_ok=True)

    def _compose_up(
        self, project: str, compose_file: Path, env: dict[str, str], up_cmd: list[str]
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["docker", "compose", "-p", project, "-f", str(compose_file), *up_cmd],
            env={**os.environ, **env},
            capture_output=True,
            text=True,
            timeout=1800,
        )

    def start_compose(self, task: TerminalTask) -> tuple[str, str, bool]:
        """启动任务容器，返回 (client_container_name, project_name, preinstalled)。

        preinstalled=True 表示 kyncode 已在镜像构建期预装（运行时无需 pip）。
        """
        logs_root = self.logs_root or Path(tempfile.mkdtemp(prefix="tb-logs-"))
        (logs_root / "sessions").mkdir(parents=True, exist_ok=True)
        (logs_root / "agent-logs").mkdir(parents=True, exist_ok=True)

        project = f"tb-eval-{uuid.uuid4().hex[:8]}"
        client_container = f"tb-eval-{task.id}-{uuid.uuid4().hex[:6]}"
        env = self._compose_env(task, client_container, logs_root)
        compose_file = task.path / "docker-compose.yaml"

        # 构建期预装路径：单 build 服务 + 单 FROM 干净基础镜像
        preinstalled = False
        build_spec = self._client_build_spec(task)
        base_image = self._extract_base_image(build_spec[0]) if build_spec else None
        if build_spec and base_image:
            image_name = self._image_name(task.id)
            already = self._docker("image", "inspect", image_name).returncode == 0
            if not (self.no_rebuild and already):
                self._build_client_image(
                    task, build_spec[0], build_spec[1], image_name, base_image
                )
            preinstalled = True
            up_cmd = ["up", "-d"]
        else:
            up_cmd = ["up", "-d"] + ([] if self.no_rebuild else ["--build"])

        r = self._compose_up(project, compose_file, env, up_cmd)
        if r.returncode != 0:
            # 偶发网络抖动（apt 源 / 镜像拉取），重试一次
            time.sleep(3)
            r = self._compose_up(project, compose_file, env, up_cmd)
        if r.returncode != 0:
            raise RuntimeError(
                f"compose 启动失败 {task.id}:\n{(r.stderr or r.stdout)[-2000:]}"
            )
        return client_container, project, preinstalled

    def stop_compose(self, project: str) -> None:
        subprocess.run(
            ["docker", "compose", "-p", project, "down", "--volumes"],
            capture_output=True,
            text=True,
        )
        if self.cleanup:
            subprocess.run(
                ["docker", "compose", "-p", project, "down", "--rmi", "all", "--volumes"],
                capture_output=True,
                text=True,
            )

    def install_agent(self, container: str, preinstalled: bool = False) -> None:
        """注入 kyncode 运行所需的 config；preinstalled=False 时还需运行时装 wheel。

        preinstalled=True 表示 kyncode 已在镜像构建期预装（运行时无需 pip，
        可支持 broken-python 这类「破坏 pip 环境」的任务）。
        """
        if not preinstalled:
            # 0. 确保 pip 可用（ubuntu 基础镜像只有 python3，无 pip）
            self._ensure_pip(container)

            # 1. wheel（保留原始文件名，pip 依赖文件名解析包名/版本）
            wheel_name = self.wheel_path.name
            r = self._docker("cp", str(self.wheel_path), f"{container}:/tmp/{wheel_name}")
            if r.returncode != 0:
                raise RuntimeError(f"wheel 拷贝失败: {r.stderr[-500:]}")
            r = self._docker(
                "exec", container, "sh", "-c",
                f"python3 -m pip install -q --break-system-packages /tmp/{wheel_name}",
                timeout=600,
            )
            if r.returncode != 0:
                raise RuntimeError(f"kyncode 安装失败:\n{r.stderr[-2000:]}")

        # 2. 精简 config（providers 来自 agent_config，mcp_servers 置空）
        slim = self._build_slim_config()
        if slim is None:
            return
        with tempfile.NamedTemporaryFile(
            "w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            yaml.safe_dump(slim, f, allow_unicode=True, sort_keys=False)
            tmp = f.name
        try:
            # 注入到 ~/.config.yaml（kyncode 第一查找位置，不依赖 cwd）；同时确保 /app 存在
            self._docker("exec", container, "sh", "-c", "mkdir -p /root /app")
            r = self._docker("cp", tmp, f"{container}:/root/config.yaml")
            if r.returncode != 0:
                raise RuntimeError(f"config 注入失败: {r.stderr[-500:]}")
        finally:
            Path(tmp).unlink(missing_ok=True)

    def _ensure_pip(self, container: str) -> None:
        """确保容器里有 pip（``python3 -m pip``）。没有则 apt 装 python3-pip。"""
        r = self._docker("exec", container, "sh", "-c", "python3 -m pip --version")
        if r.returncode == 0:
            return
        r = self._docker(
            "exec", container, "sh", "-c",
            "apt-get update -qq && apt-get install -y -qq python3-pip",
            timeout=600,
        )
        if r.returncode != 0:
            raise RuntimeError(f"pip 安装失败:\n{r.stderr[-1000:]}")

    def _build_slim_config(self) -> dict[str, Any] | None:
        if not self.agent_config_path.exists():
            return None
        try:
            raw = yaml.safe_load(self.agent_config_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            return None
        return {
            "providers": raw.get("providers", []),
            "permission_mode": raw.get("permission_mode", "default"),
            "mcp_servers": [],
        }

    def run_agent(
        self, container: str, instruction: str, timeout_sec: float
    ) -> dict[str, Any]:
        """容器内 headless 跑 kyncode，复用 adapter._parse_ndjson 解析 stream-json。"""
        r = self._docker(
            "exec", "-w", "/app", container,
            "kyncode", "-p", instruction, "--output-format", "stream-json",
            timeout=int(timeout_sec) + 30,
        )
        parsed = _parse_ndjson(r.stdout)
        parsed["trace"] = parse_trace(r.stdout)
        return parsed

    def grade(self, container: str, task: TerminalTask) -> tuple[dict[str, str], bool, str]:
        """容器内跑 run-tests.sh 并解析 pytest 结果。

        返回 ``(test_name -> passed/failed, parse_ok, 判分输出摘要)``。
        """
        # 拷 tests 目录 + run-tests.sh
        r = self._docker("cp", str(task.path / "tests"), f"{container}:/tests")
        if r.returncode != 0:
            return {}, False, f"tests 拷贝失败: {r.stderr[-500:]}"
        r = self._docker(
            "cp", str(task.path / "run-tests.sh"), f"{container}:/tests/run-tests.sh"
        )
        if r.returncode != 0:
            return {}, False, f"run-tests.sh 拷贝失败: {r.stderr[-500:]}"

        r = self._docker(
            "exec", "-e", "TEST_DIR=/tests", "-w", "/app", container,
            "bash", "/tests/run-tests.sh",
            timeout=int(task.test_timeout_sec) + 180,
        )
        output = (r.stdout or "") + "\n" + (r.stderr or "")
        results, ok = parse_pytest(output)
        return results, ok, output

    # ---- 单任务完整流程 ---------------------------------------------------

    def run_one(self, task: TerminalTask) -> TaskOutcome:
        spec, attempt = self._run_one(task)
        return TaskOutcome(spec=spec, attempts=[attempt])

    def _run_one(self, task: TerminalTask) -> tuple[TaskSpec, Attempt]:
        started = time.monotonic()
        spec = self._make_spec(task)

        # 容器阶段（docker compose 起多服务）
        try:
            container, project, preinstalled = self.start_compose(task)
        except Exception as e:  # 构建/启动失败 = 基础设施故障
            return spec, _attempt(
                task, started, success=False, infra_failure=True, detail=str(e)
            )

        try:
            try:
                self.install_agent(container, preinstalled=preinstalled)
            except Exception as e:
                return spec, _attempt(
                    task, started, success=False, infra_failure=True, detail=str(e)
                )

            # agent 阶段
            timed_out = False
            try:
                parsed = self.run_agent(container, task.instruction, task.agent_timeout_sec)
            except subprocess.TimeoutExpired:
                timed_out = True
                parsed = _parse_ndjson("")
            except Exception as e:
                return spec, _attempt(
                    task, started, success=False, infra_failure=True, detail=str(e)
                )

            # 判分阶段
            results, parse_ok, out = self.grade(container, task)
            failed = sorted(k for k, v in results.items() if v != "passed")
            passed = sorted(k for k, v in results.items() if v == "passed")
            resolved = parse_ok and bool(results) and not failed
            infra_failure = not parse_ok  # summary 段缺失 / 测试脚本跑挂 = 评测基础设施问题

            grade = GraderResult(
                resolved=resolved,
                fail_to_pass_passed=passed,  # 复用字段存「通过的测试名」
                failed=failed,
                detail=(out[-800:] if resolved is False and parse_ok else ""),
                infra_failure=infra_failure,
            )
            attempt = _attempt(
                task,
                started,
                success=resolved,
                infra_failure=infra_failure,
                timed_out=timed_out,
                parsed=parsed,
                grade=grade,
            )
            return spec, attempt
        finally:
            self.stop_compose(project)

    def _make_spec(self, task: TerminalTask) -> TaskSpec:
        return TaskSpec(
            id=task.id,
            category=task.category,
            kind="terminal",
            title=task.title,
            prompt=task.instruction,
            dataset_dir=task.path,
            setup_dir=task.path,
            tests_dir=task.path / "tests",
            timeout_seconds=int(task.agent_timeout_sec),
            max_retries=0,
            grader={"type": "terminal"},
            dataset_version=f"{DATASET_NAME}-{DATASET_VERSION}",
            difficulty=task.difficulty,
        )


def _attempt(
    task: TerminalTask,
    started: float,
    success: bool,
    infra_failure: bool = False,
    timed_out: bool = False,
    parsed: dict[str, Any] | None = None,
    grade: GraderResult | None = None,
    detail: str = "",
) -> Attempt:
    parsed = parsed or {}
    usage = parsed.get("usage", {})
    return Attempt(
        index=0,
        task_id=task.id,
        success=success,
        infra_failure=infra_failure,
        timed_out=timed_out,
        duration_seconds=round(time.monotonic() - started, 2),
        input_tokens=int(usage.get("input_tokens", 0)),
        output_tokens=int(usage.get("output_tokens", 0)),
        num_turns=int(parsed.get("num_turns", 0)),
        tool_calls=list(parsed.get("tool_calls", [])),
        errors=list(parsed.get("errors", [])),
        stop_reason=str(parsed.get("stop_reason", "")),
        final_text=str(parsed.get("result", "")),
        trace=list(parsed.get("trace", [])),
        grade=grade or GraderResult(resolved=success, detail=detail),
    )
