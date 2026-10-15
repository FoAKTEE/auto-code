"""``zkqec qualify-host`` -- M0 host qualification (prompt §11 M0; contracts §J0–§J5, §K, §O).

Every property gets a positive test (the mechanism allows what it must) and a negative test (it
refuses what it must). A property is *qualified* only if both pass. The run writes
``runs/qualify/<timestamp>/report.json`` and ``report.md`` plus all evidence (job directories with
captured, capped stdio; broker logs; the per-campaign CA certificate -- the CA key is deleted at the end).

Live model calls run only here, at minimal settings (Claude ``claude-haiku-5-5``; Codex ``gpt-6-astra``
with low effort) and are counted in the report. Use ``--no-live`` to skip them (those properties are
then reported as not run, i.e. unqualified).

Credentials are never read into this process except to build a redaction/scan list of their string
values, which is kept in memory and used only to prove that no log, report or job directory contains
them.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .agents import launch as L
from .broker import policy as BP
from .broker.bridge import Bridge
from .broker.launcher import ADDON, BASE_POLICY, MITMDUMP, POLICY_MODULE, Broker, Ceilings, mitmproxy_version, sha256_file, summarize_job
from .broker.testing import FakeUpstream, ProxyClient, local_policy_overrides, make_test_pki
from .sandbox import cgroup as CG
from .sandbox.outputs import OutputRejected, load_json_file_capped, parse_json_capped
from .sandbox.profiles import (BWRAP, ENTRY_SH, NSEXEC, PYTHON_RUNTIME, SOCAT, agent_call_profile,
                               program_profile)
from .sandbox.runner import JobResult, JobSpec, Limits, run_job

REPO = Path(__file__).resolve().parents[2]
PRICES = REPO / "configs" / "broker" / "prices.json"
RUNTIME_DOC_SHA = "473dd9c9a03e0b41ba35f986afd9bb13162d7ac7aa5d44d4dfdf75a2e19c9989"
DEFAULT_ARCHIVE = Path("/data/haiyangw/claude/zkqec-archive/wheels")
CLAUDE_MODEL = "claude-haiku-5-5"
CODEX_MODEL = "gpt-6-astra"
SCHEMA = {"type": "object", "properties": {"answer": {"type": "integer"}}, "required": ["answer"],
          "additionalProperties": False}
ROLE = "You are a calculator. Reply only with JSON that matches the output schema."
PROMPT = "What is 2+3?"
PROBE_ENV = {**L.COMMON_ENV, "PATH": "/usr/bin:/bin"}


# --------------------------------------------------------------------------------------------------
# Report structures
# --------------------------------------------------------------------------------------------------
@dataclass
class Check:
    passed: bool | None
    summary: str
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class Property:
    pid: str
    title: str
    contract: str
    description: str
    positive: Check
    negative: Check
    note: str = ""
    consequence: str = ""

    @property
    def verdict(self) -> str:
        return "qualified" if self.positive.passed is True and self.negative.passed is True else "unqualified"

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["verdict"] = self.verdict
        return d


def _short(data: bytes | str | None, n: int = 1500) -> str:
    if data is None:
        return ""
    s = data.decode("utf-8", "replace") if isinstance(data, bytes) else data
    return s if len(s) <= n else s[: n // 2] + f"\n...[{len(s) - n} chars elided]...\n" + s[-n // 2:]


def _secret_values() -> list[bytes]:
    vals: list[bytes] = []

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
        elif isinstance(x, str) and len(x) >= 20:
            vals.append(x.encode())

    for p in (L.CLAUDE_CREDENTIALS, L.CODEX_AUTH):
        try:
            walk(json.loads(p.read_text()))
        except (OSError, ValueError):
            pass
    return vals


# --------------------------------------------------------------------------------------------------
# Qualifier
# --------------------------------------------------------------------------------------------------
class Qualifier:
    def __init__(self, out: Path, *, live: bool, codex_live: bool, archive: Path) -> None:
        self.out = out
        self.live = live
        self.codex_live = codex_live
        self.archive = archive
        stamp = out.name.replace("-", "").replace(":", "")
        self.campaign = re.sub(r"[^A-Za-z0-9_]", "", "q" + stamp)[:30]
        self.jobs = out / "jobs"
        self.props: list[Property] = []
        self.secrets: list[bytes] = _secret_values()
        self.spend: dict[str, Any] = {"claude_usd": 0.0, "claude_invocations": 0, "claude_billed_calls": 0,
                                      "codex_invocations": 0, "codex_model_requests_forwarded": 0}
        self.shared: dict[str, Any] = {}
        self.cleanups: list[Callable[[], None]] = []

    # -- infrastructure --------------------------------------------------------------------------
    def setup(self) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        self.jobs.mkdir(exist_ok=True)
        self.slice = CG.CampaignSlice(self.campaign, memory_max=24 * 1024**3, tasks_max=4096)
        self.slice.create()
        self.cleanups.append(self.slice.cleanup)
        self.broker = Broker(self.campaign, self.out)
        self.broker.start()
        self.cleanups.append(self.broker.stop)

    def teardown(self) -> None:
        for fn in reversed(self.cleanups):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass
        try:
            shutil.copy(self.broker.ca_cert, self.out / "broker-ca-cert.pem")
        except OSError:
            pass
        self.broker.remove_ca_key()

    def redact(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s.decode("utf-8", "replace"), "<redacted>")
        return text

    def ev_job(self, res: JobResult, note: str = "") -> dict[str, Any]:
        return {"job": os.path.relpath(res.job_dir, self.out), "outcome": res.outcome, "exit_code": res.exit_code,
                "wall_s": round(res.wall_seconds, 2), "blocked": res.blocked_reason,
                "stdout": self.redact(_short(res.stdout)), "stderr": self.redact(_short(res.stderr, 600)),
                "cmd": self.redact(_short(" ".join(res.bwrap_argv[-6:]), 400)), "note": note}

    def grant(self, jid: str, provider: str, models: list[str], ceilings: Ceilings) -> str:
        tok = self.broker.grant(jid, provider, models, ceilings)
        self.secrets.append(tok.encode())
        return tok

    def probe(self, jid: str, script: str, *, provider: str = "none", grant_provider: str = "claude",
              home_files: tuple = (), limits: Limits = Limits(wall_seconds=60), inputs: dict | None = None,
              host_env_extra: dict | None = None, ceilings: Ceilings | None = None) -> JobResult:
        """Run a shell probe inside the agent-call profile (bridge network)."""
        ceil = ceilings or Ceilings(2, 4, 100000, 4096, 120)
        tok = self.grant(jid, grant_provider, [CLAUDE_MODEL], ceil)
        try:
            return run_job(JobSpec(jid, agent_call_profile(provider), ("/bin/sh", "-c", script), dict(PROBE_ENV),
                                   inputs=inputs or {}, home_files=home_files, limits=limits,
                                   ca_cert=str(self.broker.ca_cert), broker_port=self.broker.port, token=tok),
                           self.jobs, self.slice, blocked_probe=self.broker.blocked_reason,
                           host_env_extra=host_env_extra)
        finally:
            self.broker.revoke(tok)

    def program(self, jid: str, script: str, *, slice_: CG.CampaignSlice | None = None, tmpfs: int = 64 << 20,
                limits: Limits = Limits(wall_seconds=60), with_python: bool = False) -> JobResult:
        prof = program_profile(tmpfs_bytes=tmpfs, with_python=with_python)
        env = {"PATH": "/opt/zkqec/python/bin:/usr/bin:/bin", "HOME": "/home/agent", "LANG": "C.UTF-8"}
        return run_job(JobSpec(jid, prof, ("/bin/sh", "-c", script), env, limits=limits), self.jobs,
                       slice_ or self.slice)

    def cli_call(self, jid: str, launch: L.CliLaunch, provider: str, model: str, ceilings: Ceilings,
                 wall: int = 180) -> tuple[JobResult, list[dict[str, Any]]]:
        tok = self.grant(jid, provider, [model], ceilings)
        try:
            res = run_job(JobSpec(jid, agent_call_profile(provider), launch.argv, launch.env, inputs=launch.inputs,
                                  home_files=launch.home_files, stdin_input=launch.stdin_input,
                                  limits=Limits(wall_seconds=wall), ca_cert=str(self.broker.ca_cert),
                                  broker_port=self.broker.port, token=tok),
                          self.jobs, self.slice, blocked_probe=self.broker.blocked_reason)
        finally:
            self.broker.revoke(tok)
        recs = self.broker.records(jid)
        if provider == "claude":
            self.spend["claude_invocations"] += 1
            try:
                d = json.loads(res.stdout)
                cost = float(d.get("total_cost_usd") or 0.0)
                self.spend["claude_usd"] += cost
                if cost > 0:
                    self.spend["claude_billed_calls"] += 1
            except (ValueError, TypeError):
                pass
        else:
            self.spend["codex_invocations"] += 1
            self.spend["codex_model_requests_forwarded"] += summarize_job(recs)["model_requests"]
        return res, recs

    def add(self, prop: Property) -> None:
        self.props.append(prop)

    def run_property(self, fn: Callable[[], None]) -> None:
        name = fn.__name__
        t0 = time.time()
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            self.add(Property(f"ERROR.{name}", f"{name} crashed", "-", "qualification step raised",
                              Check(False, f"{type(exc).__name__}: {exc}",
                                    [{"traceback": self.redact(traceback.format_exc()[-3000:])}]),
                              Check(None, "not run")))
        print(f"[qualify] {name}: {time.time() - t0:.1f}s", flush=True)

    # ==============================================================================================
    # J0 process isolation
    # ==============================================================================================
    def j0_signal_and_proc(self) -> None:
        victim = subprocess.Popen(["/usr/bin/sleep", "600"], start_new_session=True)
        try:
            script = (f'kill -0 $$ && echo self_signal_ok; sleep 30 & c=$!; kill -TERM $c && echo child_signal_ok; '
                      f'kill -TERM {victim.pid} 2>&1; echo "victim_rc=$?"; '
                      f'test -e /proc/{victim.pid} && echo victim_visible || echo victim_invisible; '
                      f'echo "nproc=$(ls -d /proc/[0-9]* | wc -l)"; echo "dev=$(ls /dev | tr "\\n" " ")"')
            res = self.probe("j0_signal", script)
            out = res.stdout.decode()
            alive = victim.poll() is None
        finally:
            victim.kill()
            victim.wait()
        nproc = int(re.search(r"nproc=(\d+)", out).group(1)) if "nproc=" in out else 999
        devs = set(re.search(r"dev=(.*)", out).group(1).split()) if "dev=" in out else set()
        allowed_dev = {"core", "fd", "full", "null", "ptmx", "pts", "random", "shm", "stderr", "stdin", "stdout",
                       "tty", "urandom", "zero"}
        self.add(Property(
            "J0.signal", "Signals cannot reach outside processes", "§J0",
            "A sacrificial host process (sleep) must be unsignalable and invisible from inside the sandbox.",
            Check("self_signal_ok" in out and "child_signal_ok" in out,
                  "inside the sandbox, signalling itself and its own child works", [self.ev_job(res)]),
            Check("victim_rc=0" not in out and "victim_invisible" in out and alive,
                  f"kill -TERM <host pid {victim.pid}> failed, pid invisible in /proc, victim alive={alive}",
                  [self.ev_job(res)])))
        self.add(Property(
            "J0.proc_dev", "Private /proc and /dev", "§J0",
            "procfs shows only the sandbox's PID namespace; /dev holds only the minimal device set.",
            Check("self_signal_ok" in out and nproc >= 1, f"own processes visible (nproc={nproc})", [self.ev_job(res)]),
            Check(nproc < 16 and "victim_invisible" in out and devs <= allowed_dev,
                  f"nproc={nproc} (<16), host pid invisible, /dev={sorted(devs)}", [self.ev_job(res)])))

    def j0_env_fds(self) -> None:
        canary = "zkqec-env-canary-" + secrets.token_hex(12)
        fd_canary = self.out / "fd-canary.txt"
        fd_canary.write_text("fd canary " + secrets.token_hex(8))
        fd = os.open(fd_canary, os.O_RDONLY)
        os.set_inheritable(fd, True)
        extra = {"ZKQEC_CANARY_SECRET": canary, "ANTHROPIC_API_KEY": canary, "OPENAI_API_KEY": canary,
                 "SSH_AUTH_SOCK": "/tmp/ssh-canary/agent.1"}
        try:
            ctl = subprocess.run(["/bin/sh", "-c", "env; ls -l /proc/self/fd"], env={**CG.host_env(), **extra},
                                 pass_fds=(fd,), capture_output=True, text=True, timeout=30)
            script = 'env | sort; echo ---fds; for p in /proc/[0-9]*; do echo "== $p"; ls -l $p/fd 2>&1; done'
            res = self.probe("j0_env", script, host_env_extra=extra)
        finally:
            os.close(fd)
        out = res.stdout.decode()
        env_part = out.split("---fds")[0]
        names = {line.split("=", 1)[0] for line in env_part.splitlines() if "=" in line}
        fd_part = out.split("---fds")[1] if "---fds" in out else ""
        targets = [line.split(" -> ", 1)[1].strip() for line in fd_part.splitlines() if " -> " in line]
        ok_target = re.compile(r"^(/dev/null|pipe:\[\d+\]|socket:\[\d+\]|/proc/\d+/fd|anon_inode:.*|/dev/pts/\d+)$")
        weird = [t for t in targets if not ok_target.match(t)]
        self.add(Property(
            "J0.env_fds", "No inherited secrets in environment or descriptors", "§J0",
            "Launcher-side canary variables and an inheritable canary fd must not appear inside; the "
            "environment is exactly the allowlist.",
            Check(canary in ctl.stdout and str(fd_canary) in ctl.stdout,
                  "control: a plain host child does inherit the canary env and fd (so the test can see them)",
                  [{"cmd": "sh -c 'env; ls -l /proc/self/fd' (host, pass_fds)", "exit_code": ctl.returncode,
                    "output": "canary env present" if canary in ctl.stdout else "absent"}]),
            Check(canary not in out and str(fd_canary) not in out and names <= set(agent_call_profile("none").env_allowlist)
                  | {"PWD", "SHLVL", "_", "OLDPWD"} and not weird and len(targets) >= 6,
                  f"inside: canary absent, fd canary absent, env names={sorted(names)}; {len(targets)} open fds "
                  f"across all sandbox processes, all stdio/pipes/sockets (unexpected={weird})",
                  [self.ev_job(res)])))

    def j0_sockets(self) -> None:
        name = "zkqec-canary-" + secrets.token_hex(6)
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind("\0" + name)
        srv.listen(4)
        try:
            cli = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            host_ok = cli.connect_ex("\0" + name) == 0
            cli.close()
            bus = Path(f"/run/user/{os.getuid()}/bus")
            script = (f"/usr/bin/socat -u ABSTRACT-CONNECT:{name} - </dev/null 2>/dev/null; echo abstract_rc=$?; "
                      "for p in /run/user /run/dbus /var/run /tmp/.X11-unix /run/systemd /run/user/$(id -u)/bus; do "
                      "test -e $p && echo present:$p || echo absent:$p; done; "
                      'echo "DBUS=${DBUS_SESSION_BUS_ADDRESS:-unset} SSH=${SSH_AUTH_SOCK:-unset} '
                      'XDG=${XDG_RUNTIME_DIR:-unset} DISPLAY=${DISPLAY:-unset}"; ls /run/zkqec')
            res = self.probe("j0_sock", script)
        finally:
            srv.close()
        out = res.stdout.decode()
        self.add(Property(
            "J0.sockets", "No host control sockets", "§J0",
            "No D-Bus, systemd user bus, SSH agent, X11, /run/user; abstract Unix sockets are isolated by "
            "the network namespace. Only the egress bridge socket is mounted (/run/zkqec).",
            Check(host_ok and bus.exists(), f"control: host connects to the abstract canary socket ({host_ok}); "
                  f"host has {bus} ({bus.exists()})", [{"cmd": "host connect(\\0" + name + ")", "exit_code": 0}]),
            Check("abstract_rc=0" not in out and "present:" not in out and "DBUS=unset SSH=unset XDG=unset DISPLAY=unset" in out,
                  "inside: abstract socket unreachable, control-socket paths absent, related env unset",
                  [self.ev_job(res)])))

    def j0_descendants(self) -> None:
        jid = "j0_desc"
        holder: dict[str, JobResult] = {}
        t = threading.Thread(target=lambda: holder.setdefault("r", self.probe(
            jid, "sleep 300 & sleep 300 & (setsid sleep 300 &); echo started; sleep 300",
            limits=Limits(wall_seconds=120))))
        t.start()
        unit = self.slice.scope_unit(jid)
        pids: list[int] = []
        for _ in range(100):
            cg = CG.unit_cgroup(unit)
            if cg is not None and (cg / "cgroup.procs").exists():
                pids = [int(x) for x in (cg / "cgroup.procs").read_text().split()]
                if len(pids) >= 5:
                    break
            time.sleep(0.2)
        cmdlines = {}
        for p in pids:
            try:
                cmdlines[p] = Path(f"/proc/{p}/cmdline").read_bytes().replace(b"\0", b" ").decode()[:60]
            except OSError:
                pass
        self.slice.kill_scope(jid)
        t.join(60)
        time.sleep(1)
        survivors = [p for p, c in cmdlines.items() if _pid_alive(p, c)]
        res2 = self.probe("j0_desc_exit", "(setsid sleep 300 &); sleep 300 & echo bye")
        time.sleep(1)
        leftover = CG.unit_cgroup(self.slice.scope_unit("j0_desc_exit"))
        left_pids = []
        if leftover is not None and (leftover / "cgroup.procs").exists():
            left_pids = (leftover / "cgroup.procs").read_text().split()
        self.add(Property(
            "J0.descendants", "Descendants die with the scope", "§J0",
            "Background, setsid'ed descendants are killed when the job's scope is killed and when the job's "
            "main process exits (PID-namespace teardown).",
            Check(len(pids) >= 5, f"while running, the scope held {len(pids)} processes: {sorted(cmdlines.values())[:8]}",
                  [{"unit": unit, "pids": len(pids)}]),
            Check(not survivors and not left_pids and holder.get("r") is not None,
                  f"after kill: survivors={survivors}; after natural exit: leftover pids={left_pids}",
                  [self.ev_job(holder["r"]) if "r" in holder else {}, self.ev_job(res2)])))

    # ==============================================================================================
    # J1 broker
    # ==============================================================================================
    def j1_egress(self) -> None:
        body = json.dumps({"model": CLAUDE_MODEL, "max_tokens": 8, "messages": [{"role": "user", "content": "hi"}]})
        script = (
            "curl -sS -m 8 --noproxy '*' -o /dev/null https://api.anthropic.com/ 2>&1; echo \"direct_name_rc=$?\"; "
            "curl -sS -m 8 --noproxy '*' -o /dev/null https://1.1.1.1/ 2>&1; echo \"direct_ip_rc=$?\"; "
            "echo routes=$(tail -n +2 /proc/net/route | wc -l); "
            f"curl -sS -m 20 --cacert /opt/zkqec/ca/broker-ca.pem -o /dev/null -w 'proxied_http=%{{http_code}}\\n' "
            f"-X POST -H 'content-type: application/json' -d '{body}' https://api.anthropic.com/v1/messages")
        res = self.probe("j1_egress", script)
        out = res.stdout.decode()
        recs = self.broker.records("j1_egress")
        self.shared["egress_records"] = recs
        m = re.search(r"proxied_http=(\d+)", out)
        code = int(m.group(1)) if m else 0
        self.add(Property(
            "J1.direct_egress", "No egress except through the broker", "§J0/§J1",
            "Direct connections (by name and by IP) fail inside the sandbox; the same request through the "
            "bridge reaches the provider through the TLS-inspecting broker.",
            Check(code in (401, 403) and any(r.get("event") == "response" for r in recs),
                  f"through the broker an unauthenticated POST /v1/messages reached upstream (HTTP {code}, "
                  "no credentials sent)", [self.ev_job(res)]),
            Check("direct_name_rc=0" not in out and "direct_ip_rc=0" not in out and "routes=0" in out,
                  "direct name and IP connections fail; no routes in the sandbox netns", [self.ev_job(res)])))
        res2 = self.probe("j1_host", "curl -sS -m 10 https://example.com/ -o /dev/null 2>&1; echo \"other_rc=$?\"; "
                          "curl -sS -m 10 --cacert /opt/zkqec/ca/broker-ca.pem https://api.anthropic.com/v1/messages "
                          "-o /dev/null -w 'after=%{http_code}\\n' 2>&1")
        recs2 = self.broker.records("j1_host")
        blocked_other = any(r.get("event") == "connect" and r.get("host") == "example.com" and r.get("decision") == "block"
                            and r.get("reason") == "host_not_allowlisted" for r in recs2)
        failed = any(r.get("event") == "job_failed" for r in recs2)
        self.add(Property(
            "J1.host_allowlist", "Only provider API hosts are reachable via the broker", "§J1",
            "CONNECT to a non-allowlisted host is refused (403) and fails the job closed; later requests of "
            "the job are refused too.",
            Check(any(r.get("event") == "connect" and r.get("decision") == "allow" and r.get("host") == "api.anthropic.com"
                      for r in recs), "CONNECT api.anthropic.com:443 allowed for a granted job", [self.ev_job(res)]),
            Check(blocked_other and failed and res2.outcome == "policy_blocked",
                  f"example.com refused (host_not_allowlisted), job failed closed, outcome={res2.outcome}",
                  [self.ev_job(res2), {"broker_records": [{k: r.get(k) for k in ("event", "host", "decision", "reason")}
                                                         for r in recs2]}])))

    def j1_synthetic(self) -> None:
        """Broker mechanism against a local fake upstream: HTTP body policy, WebSocket frame policy,
        request ceilings, usage capture -- no credentials, no network."""
        d = self.out / "synthetic"
        d.mkdir(exist_ok=True)
        pki = make_test_pki(d / "pki")
        up = FakeUpstream(pki["cert"], pki["key"]).start()
        b = Broker(self.campaign + "s", d, overrides=local_policy_overrides(up.port))
        b.start(extra_args=["--set", f"ssl_verify_upstream_trusted_ca={pki['ca']}"])
        results: dict[str, Any] = {}
        try:
            def client(jid: str, provider: str, model: str, ceil: Ceilings) -> tuple[Bridge, ProxyClient]:
                tok = b.grant(jid, provider, [model], ceil)
                self.secrets.append(tok.encode())
                br = Bridge(str(d / f"{jid}.sock"), b.port, tok).start()
                return br, ProxyClient(str(d / f"{jid}.sock"), str(b.ca_cert), "localhost", up.port)

            msg = {"model": "m1", "max_tokens": 50, "messages": [{"role": "user", "content": "x"}]}
            br, c = client("s_http", "claude", "m1", Ceilings(1, 4, 4096, 100, 60))
            results["http_ok"] = c.request("POST", "/v1/messages", json.dumps(msg).encode())[0]
            results["http_ceiling"] = c.request("POST", "/v1/messages", json.dumps(msg).encode())[0]
            c.close(); br.stop()
            br, c = client("s_tool", "claude", "m1", Ceilings(3, 6, 4096, 100, 60))
            results["http_tool"] = c.request("POST", "/v1/messages", json.dumps(
                {**msg, "tools": [{"type": "web_search_20250305", "name": "web_search"}]}).encode())[0]
            c.close(); br.stop()
            br, c = client("s_big", "claude", "m1", Ceilings(3, 6, 512, 100, 60))
            results["http_big"] = c.request("POST", "/v1/messages", json.dumps({**msg, "messages": [
                {"role": "user", "content": "x" * 2000}]}).encode())[0]
            c.close(); br.stop()
            br, c = client("s_out", "claude", "m1", Ceilings(3, 6, 4096, 10, 60))
            results["http_out"] = c.request("POST", "/v1/messages", json.dumps(msg).encode())[0]
            c.close(); br.stop()
            br, c = client("s_ws", "codex", "g1", Ceilings(3, 6, 100000, 100, 60))
            results["ws_upgrade"] = c.websocket("/backend-api/codex/responses")
            c.ws_send(json.dumps({"type": "response.create", "model": "g1", "input": [
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "hi"}]}]}))
            r1 = c.ws_recv()
            results["ws_ok_reply"] = json.loads(r1[1]).get("type") if r1 else None
            c.ws_send(json.dumps({"type": "response.create", "model": "g1", "input": [
                {"type": "additional_tools", "role": "developer",
                 "tools": [{"type": "namespace", "name": "functions", "tools": [{"type": "function", "name": "exec"}]}]}]}))
            r2 = c.ws_recv()
            results["ws_tool_reply"] = json.loads(r2[1]).get("type") if r2 else None
            c.close(); br.stop()
            br, c = client("s_ws2", "codex", "g1", Ceilings(3, 6, 100000, 100, 60))
            c.websocket("/backend-api/codex/responses")
            c.ws_send(json.dumps({"type": "response.create", "model": "g1", "tools": [{"type": "web_search"}],
                                  "input": "hi"}))
            r3 = c.ws_recv()
            results["ws_websearch_reply"] = json.loads(r3[1]).get("type") if r3 else None
            c.close(); br.stop()
            seen = [(s.kind, s.path, s.size) for s in up.seen]
        finally:
            b.stop()
            up.stop()
            b.remove_ca_key()
            for key in (d / "pki").glob("*.key"):
                key.unlink()
        recs = b.records()
        self.shared["synthetic"] = {"results": results, "seen": seen, "records": recs}
        forwarded_frames = [s for s in seen if s[0] == "ws_frame"]
        http_seen = [s for s in seen if s[0] == "http"]
        reasons = {r.get("job_id"): r.get("reason") for r in recs if r.get("event") == "job_failed"}
        ev = [{"results": results, "upstream_saw": seen, "fail_reasons": reasons,
               "log": os.path.relpath(b.log_path, self.out)}]
        self.add(Property(
            "J1.body_policy", "Request-body tool policy (HTTP and WebSocket frames)", "§J1",
            "Against a local TLS fake upstream: tool-free requests/frames are forwarded; a server tool "
            "(web_search), a top-level tools key and an `additional_tools` input item are blocked before "
            "forwarding and fail the job closed.",
            Check(results.get("http_ok") == 200 and results.get("ws_upgrade") == 101 and
                  results.get("ws_ok_reply") == "response.completed" and len(forwarded_frames) == 1,
                  "tool-free HTTP request and WebSocket response.create frame forwarded and answered", ev),
            Check(results.get("http_tool") == 403 and results.get("ws_tool_reply") == "error" and
                  results.get("ws_websearch_reply") == "error" and len(http_seen) == 1 and len(forwarded_frames) == 1
                  and reasons.get("s_tool", "").startswith("server_tool") and
                  reasons.get("s_ws", "").startswith("tool_not_allowed") and
                  reasons.get("s_ws2", "").startswith("tool_not_allowed"),
                  "web_search server tool → 403; additional_tools frame and tools-key frame dropped with an "
                  "injected error; none reached upstream; each job failed closed", ev)))
        self.add(Property(
            "K.ceilings_synthetic", "Request, input and output ceilings at the broker", "§K",
            "Per-invocation model-request ceiling, input bound (body bytes ≥ tokens) and declared output "
            "ceiling (max_tokens) are enforced before forwarding.",
            Check(results.get("http_ok") == 200, "first request within all ceilings forwarded", ev),
            Check(results.get("http_ceiling") == 403 and results.get("http_big") == 403 and results.get("http_out") == 403
                  and reasons.get("s_http") == "model_request_ceiling" and reasons.get("s_big") == "input_bound"
                  and reasons.get("s_out") == "output_ceiling",
                  "2nd request over max_model_requests=1, 2 KB body over a 512 B input bound, max_tokens 50 over a "
                  "10-token ceiling: all 403 before forwarding", ev)))

    # ==============================================================================================
    # Live CLI calls
    # ==============================================================================================
    def live_claude(self) -> None:
        if not self.live:
            for pid in ("J1.claude_call", "J2.claude_tool_free", "J2.structured_output_claude", "K.input_bound",
                        "K.output_ceiling", "K.reservation", "J5.telemetry", "J1.telemetry_endpoints"):
                self.add(Property(pid, pid, "-", "live test", Check(None, "not run (--no-live)"), Check(None, "not run")))
            return
        spec = L.CallSpec("claude", CLAUDE_MODEL, ROLE, PROMPT, SCHEMA, max_budget_usd=0.05, max_output_tokens=1024)
        ceil = Ceilings(max_model_requests=3, max_total_requests=8, max_input_bytes=400_000, max_output_tokens=1024,
                        ttl_seconds=300)
        res, recs = self.cli_call("claude_ok", L.claude_call(spec), "claude", CLAUDE_MODEL, ceil)
        summ = summarize_job(recs)
        cli: dict[str, Any] = {}
        try:
            cli = json.loads(res.stdout)
        except ValueError:
            pass
        so = cli.get("structured_output")
        self.shared["claude_ok"] = (res, recs, cli)
        # negatives (all blocked before any billable forward)
        res_t, recs_t = self.cli_call("claude_tools", L.claude_call(spec, include_tools_flag=False), "claude",
                                      CLAUDE_MODEL, ceil)
        summ_t = summarize_job(recs_t)
        tools_t = sorted({t for r in recs_t if r.get("model_request") for t in (r.get("tools") or [])})
        res_i, recs_i = self.cli_call("claude_inbound", L.claude_call(spec), "claude", CLAUDE_MODEL,
                                      Ceilings(3, 8, 2048, 1024, 300))
        res_o, recs_o = self.cli_call("claude_outcap", L.claude_call(spec), "claude", CLAUDE_MODEL,
                                      Ceilings(3, 8, 400_000, 512, 300))
        allowed_tools = set(self.broker_policy().providers["claude"].allowed_tools)
        ok = res.outcome == "completed" and so == {"answer": 5}
        self.add(Property(
            "J1.claude_call", "Allowed Claude call succeeds through sandbox + broker", "§J1/§J3",
            "claude -p in call mode (fresh home, no tools, strict empty MCP, replaced system prompt) runs in the "
            "agent-call sandbox; its only model request is inspected and forwarded.",
            Check(ok and summ["model_requests"] == 1 and summ["failed"] is None,
                  f"outcome={res.outcome}, structured_output={so}, broker model requests={summ['model_requests']}, "
                  f"cost=${cli.get('total_cost_usd')}", [self.ev_job(res), {"broker_summary": summ}]),
            Check(res_t.outcome == "policy_blocked" and summ_t["model_requests"] == 0,
                  f"the same call without --tools \"\" declares {tools_t} and is blocked before forwarding "
                  f"({res_t.blocked_reason})", [self.ev_job(res_t), {"broker_summary": summ_t}])))
        self.add(Property(
            "J2.claude_tool_free", "Claude runs tool-free (verified from request bodies)", "§J2",
            "The effective tool manifest, read from the intercepted /v1/messages body, is ⊆ {StructuredOutput, "
            "DeferredToolPlaceholder}; no server tools, no MCP servers; CLI reports no server tool use.",
            Check(ok and set(summ["declared_tools"]) <= allowed_tools and
                  not any(v for k, v in ((cli.get("usage") or {}).get("server_tool_use") or {}).items()),
                  f"declared tools {summ['declared_tools']}; server_tool_use={(cli.get('usage') or {}).get('server_tool_use')}",
                  [{"broker_summary": summ}]),
            Check(res_t.outcome == "policy_blocked" and bool(set(tools_t) - allowed_tools),
                  f"without --tools \"\": {len(tools_t)} tools declared ({tools_t[:12]}...) → blocked",
                  [{"broker_summary": summ_t}])))
        schema_ok, schema_bad = _validate(so, SCHEMA), _validate({"answer": "five"}, SCHEMA)
        caps_rejected = _caps_reject()
        self.add(Property(
            "J2.structured_output_claude", "Claude structured output", "§J3",
            "--json-schema yields a schema-valid `structured_output`; schema-invalid or cap-violating output is "
            "rejected on the trusted side.",
            Check(ok and schema_ok, f"structured_output={so} validates against the schema", [self.ev_job(res)]),
            Check(not schema_bad and caps_rejected, "an invalid instance fails validation; oversize/deep/"
                  "non-regular outputs are rejected before parsing", [{"caps_rejected": caps_rejected}])))
        in_bytes = summ["max_input_bytes_seen"]
        self.add(Property(
            "K.input_bound", "Input bound enforced at the broker (live)", "§K",
            "The request body's UTF-8 byte count (≥ tokens) is checked against the grant's input ceiling before "
            "forwarding.",
            Check(ok and 0 < in_bytes <= 400_000, f"body {in_bytes} B ≤ ceiling 400000 B → forwarded",
                  [{"broker_summary": summ}]),
            Check(res_i.outcome == "policy_blocked" and res_i.blocked_reason == "input_bound",
                  f"same call with a 2048 B ceiling → {res_i.blocked_reason}", [self.ev_job(res_i)])))
        mt = [r.get("max_output_tokens") for r in recs if r.get("model_request")]
        self.add(Property(
            "K.output_ceiling", "Output-token ceiling (CLI setting checked at the broker)", "§K",
            "Claude: CLAUDE_CODE_MAX_OUTPUT_TOKENS sets max_tokens, which the broker checks against the grant. "
            "Codex: its frames carry no output-token field, so the ceiling is not enforceable there (recorded).",
            Check(ok and mt == [1024], f"max_tokens={mt} ≤ 1024 → forwarded", [{"max_tokens": mt}]),
            Check(res_o.outcome == "policy_blocked" and res_o.blocked_reason == "output_ceiling",
                  f"grant ceiling 512 < declared 1024 → {res_o.blocked_reason}", [self.ev_job(res_o)]),
            note="Codex: output_field=absent in every response.create frame; output ceiling unenforceable for Codex."))
        prices = json.loads(PRICES.read_text())["models"].get(CLAUDE_MODEL, {})
        grant = BP.Grant("x", "claude", frozenset([CLAUDE_MODEL]), ceil.max_model_requests, ceil.max_total_requests,
                         ceil.max_input_bytes, ceil.max_output_tokens, 0)
        reserve = BP.reservation_bound(grant, max(prices.get("input") or 0, prices.get("cache_write") or 0),
                                       prices.get("output") or 0)
        cost = float(cli.get("total_cost_usd") or 0)
        blocked_potential = BP.reservation_bound(BP.Grant("y", "claude", frozenset(), 1, 1, in_bytes, 1024, 0),
                                                 max(prices.get("input") or 0, prices.get("cache_write") or 0),
                                                 prices.get("output") or 0)
        self.add(Property(
            "K.reservation", "Full-charge reservation bounds the actual charge", "§K",
            "Reservation = model-request ceiling × (input bound × highest input price incl. cache write + output "
            "ceiling × output price) from the declared price table; the realised CLI-reported charge must not "
            "exceed it, and requests that would break a ceiling never reach upstream.",
            Check(ok and 0 < cost <= reserve, f"reported ${cost:.6f} ≤ reservation ${reserve:.4f}",
                  [{"reservation_usd": reserve, "reported_usd": cost, "prices": prices}]),
            Check(res_i.outcome == "policy_blocked" and res_o.outcome == "policy_blocked" and
                  summarize_job(recs_i)["model_requests"] == 0 and summarize_job(recs_o)["model_requests"] == 0,
                  "ceiling-violating requests were never forwarded (0 model requests, nothing to settle)",
                  [{"would_be_bound_usd": blocked_potential}]),
            note="Prices are declared M0 values derived from CLI reports (configs/broker/prices.json); Codex has "
                 "no declared price (subscription)."))
        bu = summ["usage"] if isinstance(summ["usage"], dict) else {}
        cu = cli.get("usage") or {}
        match = all(bu.get(k) == cu.get(k) for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                                     "cache_read_input_tokens"))
        egress = summarize_job(self.shared.get("egress_records", []))
        self.add(Property(
            "J5.telemetry", "Telemetry complete; missing usage is unknown, never zero", "§J5",
            "Broker log (authoritative) records usage from the response stream; it must equal the CLI's JSON "
            "usage. A response without usage is recorded as `unknown`.",
            Check(ok and match and summ["usage_missing"] == 0,
                  f"broker usage {bu} == CLI usage; usage records={summ['usage_records']}",
                  [{"broker_usage": bu, "cli_usage": {k: cu.get(k) for k in bu}}]),
            Check(egress.get("usage") == "unknown",
                  f"unauthenticated (401) request: broker usage = {egress.get('usage')!r}", [{"broker_summary": egress}])))
        quiet = [r for r in recs if r.get("decision") == BP.BLOCK_EXPECTED]
        quiet_paths = sorted({r.get("path") for r in quiet})
        reached = [r for r in recs if r.get("event") == "response" and r.get("path") in quiet_paths]
        self.add(Property(
            "J1.telemetry_endpoints", "Telemetry / discovery endpoints blocked without breaking calls", "§J1",
            "Claude: GET /api/oauth/profile and GET /v1/mcp_servers are refused; Codex: analytics-events and "
            "ab.chatgpt.com OTLP are refused. Calls must still succeed.",
            Check(ok and {"/api/oauth/profile", "/v1/mcp_servers"} <= set(quiet_paths),
                  f"Claude call succeeded while {quiet_paths} were refused", [{"blocked": quiet_paths}]),
            Check(not reached, "no refused endpoint produced an upstream response", [{"reached": len(reached)}]),
            note="Codex: with analytics-events and ab.chatgpt.com blocked, Codex calls completed in the M0 dev runs "
                 "(runs/qualify/dev/x1, x3–x6) before the additional_tools check was added."))

    def live_codex(self) -> None:
        if not (self.live and self.codex_live):
            for pid in ("J2.codex_tool_free", "J2.structured_output_codex", "J1.codex_frames_live"):
                self.add(Property(pid, pid, "-", "live test", Check(None, "not run (--no-live/--no-codex)"),
                                  Check(None, "not run")))
            return
        spec = L.CallSpec("codex", CODEX_MODEL, ROLE, PROMPT, SCHEMA, effort="low")
        res, recs = self.cli_call("codex_ok", L.codex_call(spec), "codex", CODEX_MODEL,
                                  Ceilings(4, 14, 400_000, 32_000, 300))
        summ = summarize_job(recs)
        frames = [r for r in recs if r.get("event") == "ws_frame"]
        tools = sorted({t for r in frames for t in (r.get("tools") or [])})
        shapes = frames[0].get("input_shape") if frames else None
        out_ok = False
        try:
            out_ok = load_json_file_capped(Path(res.job_dir) / "out" / "last_message.json", max_bytes=65536) == {"answer": 5}
        except (OutputRejected, OSError):
            pass
        self.add(Property(
            "J2.codex_tool_free", "Codex runs tool-free (verified from request bodies)", "§J2",
            "codex exec with every tool-bearing feature disabled and web search disabled must send model requests "
            "that declare no tools (top-level `tools` or `additional_tools` input items).",
            Check(res.outcome == "completed" and not tools,
                  f"declared tools from intercepted frames: {tools}; outcome={res.outcome}",
                  [self.ev_job(res), {"frame_shape": shapes, "broker_summary": summ}]),
            Check(res.outcome == "policy_blocked" or not tools,
                  "a tool-declaring frame is blocked before forwarding and the job fails closed",
                  [{"blocked_reason": res.blocked_reason}]),
            consequence="Codex is disqualified for K0 (§4.9, D16): live Codex runs are dev-only, labelled "
                        "K0-unenforced, unless the lead accepts one of the options in notes/build/m0.md."))
        self.add(Property(
            "J1.codex_frames_live", "Broker inspects Codex WebSocket frames (live)", "§J1",
            "The live response.create frame is parsed; tool declarations inside `input` are found and the frame is "
            "dropped before reaching chatgpt.com.",
            Check(bool(frames), f"{len(frames)} response.create frame(s) inspected; keys={frames[0].get('body_keys') if frames else None}",
                  [{"frame_shape": shapes}]),
            Check(res.outcome == "policy_blocked" and summ["model_requests"] == 0 if tools else True,
                  f"tools {tools} → frame dropped, 0 frames forwarded, job killed ({res.blocked_reason})",
                  [self.ev_job(res)])))
        self.add(Property(
            "J2.structured_output_codex", "Codex structured output under the K0 policy", "§J3",
            "--output-schema yields schema-valid JSON in the -o file, collected as a regular capped file.",
            Check(out_ok, "last_message.json collected and equal to {'answer': 5}" if out_ok else
                  "not produced: the call is blocked by the K0 tool policy", [{"collected": res.collected}]),
            Check(_caps_reject(), "cap-violating outputs rejected", []),
            note="Dev runs before the additional_tools check (runs/qualify/dev/x1,x3–x6) produced {'answer': 5} via "
                 "--output-schema, so the mechanism works when the call is allowed."))

    def broker_policy(self) -> BP.CampaignPolicy:
        return BP.load_policy(str(self.broker.policy_path))

    # ==============================================================================================
    # J2 filesystem, homes
    # ==============================================================================================
    def j2_canaries(self) -> None:
        tag = "ZKQEC-CANARY-" + secrets.token_hex(10)
        planted = [self.out / "canary_repo.txt", self.out / "vault" / "canary.txt", self.jobs / "other_job_canary.txt",
                   REPO / "notes" / "build" / f".qualify-canary-{self.campaign}", Path(f"/dev/shm/zkqec-canary-{self.campaign}")]
        for p in planted:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(tag + "\n")
        existing = [REPO / "AGENTS.md", REPO / "prompt" / "00-master-prompt.md", REPO / ".git" / "HEAD", REPO / "ref-code",
                    REPO / "notes" / "PROGRESS.md", L.CLAUDE_CREDENTIALS, L.CODEX_AUTH, Path.home() / ".claude",
                    Path.home() / ".ssh", Path.home() / ".bash_history", Path("/data/haiyangw/claude/zkqec-archive")]
        paths = [str(p) for p in planted + existing] + [str(Path.home()), str(REPO)]
        try:
            script = ("for p in " + " ".join(f"'{p}'" for p in paths) + "; do test -e \"$p\" && echo \"visible:$p\" "
                      "|| echo \"invisible:$p\"; done; echo ---grep; "
                      f"grep -rlF '{tag}' /home /job /tmp /etc /run /dev/shm /opt/zkqec/ca 2>/dev/null; echo grep_done")
            res = self.probe("j2_canary", script, provider="claude", inputs={"control.txt": (tag + "\n").encode()})
        finally:
            for p in planted:
                try:
                    p.unlink()
                except OSError:
                    pass
        out = res.stdout.decode()
        visible = [line for line in out.splitlines() if line.startswith("visible:")]
        grep_hits = out.split("---grep")[-1].split("grep_done")[0].split()
        self.add(Property(
            "J2.canaries", "Filesystem allowlist: canaries stay unreadable", "§J2",
            "Canaries planted in the repo, notes, a vault dir, another job's area and /dev/shm, plus the real "
            "repo, prompts, ref-code, git, credentials, ~/.claude and $HOME, are invisible inside the agent-call "
            "sandbox; a full-text search of every writable/visible area finds only the job's own input.",
            Check(grep_hits == ["/job/in/control.txt"], f"control canary in /job/in found by the search: {grep_hits}",
                  [self.ev_job(res)]),
            Check(not visible and grep_hits == ["/job/in/control.txt"],
                  f"{len(paths)} forbidden paths invisible (visible={visible})", [self.ev_job(res)])))

    def j2_fresh_home(self) -> None:
        hostc = sorted(p.name for p in (Path.home() / ".claude").iterdir()) if (Path.home() / ".claude").exists() else []
        hostx = sorted(p.name for p in (Path.home() / ".codex").iterdir()) if (Path.home() / ".codex").exists() else []
        script = "find /home/agent -printf '%P|%m|%y\\n' | sort; test -r /home/agent/{p} && echo readable"
        lc = L.claude_call(L.CallSpec("claude", CLAUDE_MODEL, ROLE, PROMPT, SCHEMA))
        lx = L.codex_call(L.CallSpec("codex", CODEX_MODEL, ROLE, PROMPT, SCHEMA))
        rc = self.probe("j2_home_claude", script.format(p=".claude/.credentials.json"), provider="claude",
                        home_files=lc.home_files)
        rx = self.probe("j2_home_codex", script.format(p=".codex/auth.json"), provider="codex",
                        home_files=lx.home_files)
        lines_c = [x for x in rc.stdout.decode().splitlines() if x and x != "readable"]
        lines_x = [x for x in rx.stdout.decode().splitlines() if x and x != "readable"]
        exp_c = ["|700|d", ".claude/.credentials.json|600|f", ".claude|700|d"]
        exp_x = ["|700|d", ".codex/auth.json|600|f", ".codex|700|d"]
        leaked = [str(p) for p in self.out.rglob("*") if p.name in (".credentials.json", "auth.json")]
        priv_nonempty = [str(p) for p in self.jobs.glob("*/priv/*")]
        self.add(Property(
            "J2.fresh_home", "Fresh per-job CLI homes holding only the credential", "§J2/D15",
            "The job home is a private size-limited tmpfs with exactly the credential file (0600, dir 0700); host "
            "settings, CLAUDE.md, memories, plugins, skills, MCP config and sessions are absent; nothing remains on "
            "disk after the job.",
            Check(sorted(lines_c) == sorted(exp_c) and sorted(lines_x) == sorted(exp_x) and
                  b"readable" in rc.stdout and b"readable" in rx.stdout,
                  f"claude home={lines_c}; codex home={lines_x}", [self.ev_job(rc), self.ev_job(rx)]),
            Check(len(hostc) > 1 and not leaked and not priv_nonempty,
                  f"host ~/.claude has {len(hostc)} entries and ~/.codex {len(hostx)} (none copied); no credential "
                  f"file left under the run dir ({leaked}); private mount points empty on the host",
                  [{"host_claude_entries": len(hostc), "host_codex_entries": len(hostx)}])))

    # ==============================================================================================
    # J4 resources
    # ==============================================================================================
    def j4_slice(self) -> None:
        ctrl = CG.controllers()
        sm = CG.CampaignSlice(self.campaign + "m", memory_max=192 * 1024**2, tasks_max=48, cpu_quota_percent=50)
        sm.create()
        self.cleanups.append(sm.cleanup)
        lim = sm.limits()
        alloc = "/opt/zkqec/python/bin/python3 -c 'b=bytearray({n}*1024*1024); b[::4096]=b\"x\"*len(b[::4096]); print(\"alloc_ok\")'"
        r_ok = self.program("j4_mem_ok", alloc.format(n=32), slice_=sm, with_python=True)
        r_big = self.program("j4_mem_big", alloc.format(n=600), slice_=sm, with_python=True)
        events = {}
        try:
            for line in (sm.cgroup() / "memory.events").read_text().splitlines():
                k, v = line.split()
                events[k] = int(v)
        except OSError:
            pass
        self.add(Property(
            "J4.slice_memory", "Campaign slice MemoryMax (aggregate, incl. tmpfs)", "§J4/D14",
            "Jobs run in scopes inside zkqec-<campaign>.slice; the slice's MemoryMax kills a job that exceeds it.",
            Check(r_ok.outcome == "completed" and b"alloc_ok" in r_ok.stdout and lim.get("memory.max") == str(192 * 1024**2),
                  f"32 MiB allocation completes; slice memory.max={lim.get('memory.max')}", [self.ev_job(r_ok)]),
            Check(r_big.outcome != "completed" and b"alloc_ok" not in r_big.stdout and events.get("oom_kill", 0) >= 1,
                  f"600 MiB allocation killed (outcome={r_big.outcome}, rc={r_big.exit_code}); memory.events={events}",
                  [self.ev_job(r_big)])))
        spawn = "( i=0; while [ $i -lt {k} ]; do i=$((i+1)); sleep 30 & done ) 2>/dev/null; " \
                "set -- /proc/[0-9]*; echo procs=$#"
        r_t_ok = self.program("j4_tasks_ok", spawn.format(k=10), slice_=sm)
        r_t_big = self.program("j4_tasks_big", spawn.format(k=120), slice_=sm)
        pr_ok = _int_after(r_t_ok.stdout, "procs=")
        pr_big = _int_after(r_t_big.stdout, "procs=")
        pev = (sm.cgroup() / "pids.events").read_text().strip() if (sm.cgroup() / "pids.events").exists() else ""
        self.add(Property(
            "J4.slice_tasks", "Campaign slice TasksMax", "§J4/D14",
            "The slice's TasksMax bounds the number of tasks across all jobs.",
            Check(pr_ok is not None and pr_ok >= 10 and lim.get("pids.max") == "48",
                  f"10 background tasks fine (procs={pr_ok}); slice pids.max={lim.get('pids.max')}", [self.ev_job(r_t_ok)]),
            Check(pr_big is None or pr_big < 60, f"120 requested → procs={pr_big} (bounded); pids.events: {pev}",
                  [self.ev_job(r_t_big)])))
        show = subprocess.run([CG.SYSTEMCTL, "--user", "show", sm.name, "-p", "CPUQuotaPerSecUSec", "--value"],
                              capture_output=True, text=True, env=CG.host_env()).stdout.strip()
        cpu_enforced = lim.get("cpu.max") is not None and "cpu" in ctrl.get("cgroup.subtree_control", [])
        self.add(Property(
            "J4.slice_cpu", "Campaign slice CPUQuota", "§J4/D14",
            "CPUQuota must be enforced on the slice (cgroup cpu.max).",
            Check(show not in ("", "infinity"), f"systemd accepted CPUQuota (CPUQuotaPerSecUSec={show})",
                  [{"cmd": f"systemctl --user show {sm.name} -p CPUQuotaPerSecUSec", "output": show}]),
            Check(cpu_enforced, f"user manager controllers={ctrl}; slice cpu.max={lim.get('cpu.max')} → "
                  f"{'enforced' if cpu_enforced else 'NOT enforced'}", [{"controllers": ctrl}]),
            consequence="Aggregate CPU is not bounded by the kernel for user slices on this host (no cpu controller "
                        "delegated to user@.service). Mitigation: concurrency caps (D14: ≤4 calls/provider), "
                        "per-process RLIMIT_CPU in the program profile, wall-time limits. Full fix needs root: "
                        "Delegate=cpu for user@.service."))
        r_fs_ok = self.program("j4_tmpfs_ok", "dd if=/dev/zero of=/job/work/a bs=1M count=8 2>&1 | tail -1; echo rc=$?",
                               tmpfs=32 << 20)
        r_fs_big = self.program("j4_tmpfs_big", "dd if=/dev/zero of=/job/work/a bs=1M count=64 2>&1; "
                                "df -k /job/work | tail -1", tmpfs=32 << 20)
        self.add(Property(
            "J4.tmpfs_disk", "Disk bounded by a size-limited tmpfs", "§J4",
            "All writable sandbox paths live on one private tmpfs with size= (mounted by nsexec; bwrap 0.6.1 has no "
            "--size). Writing past it fails with ENOSPC; the host disk is untouched.",
            Check(r_fs_ok.outcome == "completed" and b"8388608 bytes" in r_fs_ok.stdout, "8 MiB write into a 32 MiB tmpfs ok",
                  [self.ev_job(r_fs_ok)]),
            Check(b"No space left" in r_fs_big.stdout + r_fs_big.stderr, "64 MiB write fails with ENOSPC",
                  [self.ev_job(r_fs_big)])))
        r_w_ok = self.program("j4_wall_ok", "sleep 1; echo done", limits=Limits(wall_seconds=30))
        r_w_kill = self.program("j4_wall_kill", "sleep 120; echo late", limits=Limits(wall_seconds=3))
        self.add(Property(
            "J4.job_walltime", "Per-job wall time via scope RuntimeMaxSec", "§J4",
            "A job past its RuntimeMaxSec is killed by systemd.",
            Check(r_w_ok.outcome == "completed", f"1 s job under a 30 s limit completes ({r_w_ok.wall_seconds:.1f}s)",
                  [self.ev_job(r_w_ok)]),
            Check(r_w_kill.outcome == "timeout" and r_w_kill.wall_seconds < 15 and b"late" not in r_w_kill.stdout,
                  f"120 s job under a 3 s limit killed after {r_w_kill.wall_seconds:.1f}s (outcome={r_w_kill.outcome})",
                  [self.ev_job(r_w_kill)])))

    def j4_deadline(self) -> None:
        camp = self.campaign + "d"
        sd = CG.CampaignSlice(camp, memory_max=4 * 1024**3, tasks_max=512)
        sd.create()
        self.cleanups.append(sd.cleanup)
        bd = Broker(camp, self.out / "deadline")
        bd.start()
        self.cleanups.append(bd.remove_ca_key)
        self.cleanups.append(bd.stop)
        deadline = time.time() + 12
        sup = CG.DeadlineSupervisor(sd, bd.switch_path, deadline)
        sup.arm()
        self.cleanups.append(sup.disarm)
        holders: dict[str, JobResult] = {}

        def run(jid: str) -> None:
            tok = bd.grant(jid, "claude", [CLAUDE_MODEL], Ceilings(1, 2, 1000, 10, 600))
            self.secrets.append(tok.encode())
            holders[jid] = run_job(JobSpec(jid, agent_call_profile("none"), ("/bin/sh", "-c", "sleep 300"),
                                           dict(PROBE_ENV), limits=Limits(wall_seconds=300), ca_cert=str(bd.ca_cert),
                                           broker_port=bd.port, token=tok), self.jobs, sd)

        threads = [threading.Thread(target=run, args=(f"dl_job{i}",)) for i in range(2)]
        for t in threads:
            t.start()
        abandoned = subprocess.run(sd.service_argv("dl_abandoned", runtime_max_sec=600) + ["/usr/bin/sleep", "300"],
                                   capture_output=True, text=True, env=CG.host_env())
        tok = bd.grant("dl_probe", "claude", [CLAUDE_MODEL], Ceilings(1, 4, 1000, 10, 600))
        self.secrets.append(tok.encode())
        sock = str(self.out / "deadline" / "probe.sock")
        br = Bridge(sock, bd.port, tok).start()
        time.sleep(3)
        units_before = sd.units()
        pids_before = sd.pids()
        c = ProxyClient(sock, str(bd.ca_cert), "api.anthropic.com", 443)
        before_status = c.connect_status
        c.close()
        while time.time() < deadline + 25 and (sd.pids() or not bd.dispatch_disabled()):
            time.sleep(0.5)
        fired_after = time.time() - deadline
        units_after = [u for u in sd.units() if not u.endswith(("-deadline.timer", "-deadline.service"))]
        pids_after = sd.pids()
        c2 = ProxyClient(sock, str(bd.ca_cert), "api.anthropic.com", 443)
        after_status = c2.connect_status
        c2.close()
        br.stop()
        for t in threads:
            t.join(30)
        recs = bd.records("dl_probe")
        self.add(Property(
            "J4.deadline_supervisor", "Campaign deadline supervisor", "§J4",
            "A transient systemd timer (independent of the orchestrator) fires at the campaign deadline, writes the "
            "broker's dispatch-disabled switch and stops the slice: every concurrent and abandoned job dies and the "
            "broker refuses further dispatch.",
            Check(len(pids_before) >= 3 and before_status == 200 and abandoned.returncode == 0,
                  f"before the deadline: units={units_before}, {len(pids_before)} processes, broker CONNECT={before_status}",
                  [{"units": units_before}]),
            Check(not pids_after and not units_after and bd.dispatch_disabled() and after_status == 403 and
                  any(r.get("reason") == "dispatch_disabled" for r in recs),
                  f"{fired_after:+.1f}s after the deadline: processes={len(pids_after)}, units={units_after}, "
                  f"switch={bd.dispatch_disabled()}, broker CONNECT={after_status} (dispatch_disabled); job outcomes="
                  f"{ {k: v.outcome for k, v in holders.items()} }",
                  [{"job_outcomes": {k: v.outcome for k, v in holders.items()}}])))

    def j4_output_caps(self) -> None:
        r_cap = self.program("j4_stdout_cap", "yes zkqec | head -c 3000000; echo end",
                             limits=Limits(wall_seconds=60, stdout_cap=65536))
        script = ("cd /job/out; echo '{\"ok\": 1}' > a_ok.json; head -c 2000000 /dev/zero > big.bin; "
                  "ln -s /etc/passwd link.json; mkfifo fifo.json; mkdir sub; echo x > 'bad name!'; "
                  "for i in 1 2 3 4 5 6 7 8 9 10 11 12; do echo $i > f$i.txt; done; echo made")
        r_col = self.program("j4_collect", script, limits=Limits(wall_seconds=60, collect_max_files=8,
                                                                    collect_max_file_bytes=1 << 20))
        col = r_col.collected or {}
        acc = [a["name"] for a in col.get("accepted", [])]
        rej = {(x.get("name") or "?"): x["reason"] for x in col.get("rejected", [])}
        parsed = None
        try:
            parsed = load_json_file_capped(Path(r_col.job_dir) / "out" / "a_ok.json", max_bytes=1024)
        except OutputRejected:
            pass
        caps = _caps_reject()
        self.add(Property(
            "J4.output_caps", "Output caps before parsing; only regular files", "§J4",
            "stdout/stderr are drained under byte caps (overflow kills the scope); outputs are collected only as "
            "regular files under per-file, count and total caps; JSON is parsed only after byte and depth caps.",
            Check(parsed == {"ok": 1} and "a_ok.json" in acc, f"accepted={acc}; a_ok.json parsed={parsed}",
                  [self.ev_job(r_col), {"collected": col}]),
            Check(r_cap.outcome == "output_cap" and len(r_cap.stdout) <= 65536 and rej.get("big.bin") == "file_too_large"
                  and rej.get("link.json") == "not_regular_file" and rej.get("fifo.json") == "not_regular_file"
                  and rej.get("sub") == "not_regular_file" and "?" in rej and len(acc) <= 8 and caps,
                  f"3 MB stdout → outcome={r_cap.outcome}, kept {len(r_cap.stdout)} B; rejected={rej}",
                  [self.ev_job(r_cap)])))

    # ==============================================================================================
    # J1 logging
    # ==============================================================================================
    def j1_logs(self) -> None:
        logs = sorted(self.out.rglob("broker-log.jsonl"))
        per_log: list[list[dict[str, Any]]] = []
        for lp in logs:
            recs_l = []
            for line in lp.read_bytes().splitlines():
                try:
                    recs_l.append(json.loads(line))
                except ValueError:
                    pass
            per_log.append(recs_l)
        n_recs = sum(len(r) for r in per_log)
        scanned, hits = 0, []
        header_words = [b'"authorization"', b'"cookie"', b'"proxy-authorization"', b"bearer ", b"set-cookie"]
        for p in self.out.rglob("*"):
            if p.is_file() and not p.is_symlink() and p.stat().st_size < 64 << 20:
                data = p.read_bytes()
                scanned += 1
                for sv in self.secrets:
                    if sv in data:
                        hits.append(str(p.relative_to(self.out)))
                low = data.lower()
                if p.suffix in (".jsonl", ".out") and any(w in low for w in header_words):
                    hits.append(str(p.relative_to(self.out)) + ":header-word")
        order_bad, blocked, blocked_answered, n_resp = [], [], [], 0
        for recs in per_log:
            first_allow: dict[tuple[Any, Any], int] = {}
            for r in recs:
                if r.get("event") in ("request", "ws_frame") and r.get("decision") == BP.ALLOW:
                    first_allow.setdefault((r.get("job_id"), r.get("path")), r["seq"])
            answered = set()
            for r in recs:
                if r.get("event") == "response":
                    n_resp += 1
                    key = (r.get("job_id"), r.get("path"))
                    answered.add(key)
                    if key not in first_allow or first_allow[key] > r["seq"]:
                        order_bad.append(r["seq"])
            b = [r for r in recs if r.get("event") == "request" and r.get("decision") in (BP.BLOCK, BP.BLOCK_EXPECTED)]
            blocked += b
            blocked_answered += [r for r in b if (r.get("job_id"), r.get("path")) in answered
                                 and (r.get("job_id"), r.get("path")) not in first_allow]
        self.add(Property(
            "J1.log_sanitized", "Broker logs and all evidence are sanitized", "§J1/§M",
            "No credential value, proxy token, auth header or cookie appears in the broker logs, mitmdump output, "
            "job directories or this report.",
            Check(n_recs > 20 and len(self.secrets) > 2, f"{n_recs} records in {len(logs)} broker logs; "
                  f"{len(self.secrets)} secret values (credentials + proxy tokens) loaded for the scan",
                  [{"logs": [str(x.relative_to(self.out)) for x in logs]}]),
            Check(not hits, f"scanned {scanned} files: {len(hits)} hits {hits[:5]}", [])))
        self.add(Property(
            "J1.log_before_forward", "Decision logged (fsync) before forwarding", "§L",
            "Every upstream response is preceded in the log by the allow decision of its request; blocked requests "
            "never produce an upstream response.",
            Check(n_resp > 0 and not order_bad, f"{n_resp} responses, each preceded by its allow record", []),
            Check(len(blocked) > 0 and not blocked_answered,
                  f"{len(blocked)} blocked requests, none answered upstream", [])))

    # ==============================================================================================
    # §O pinning
    # ==============================================================================================
    def o_pinning(self) -> None:
        py = PYTHON_RUNTIME / "bin" / "python3.10"
        venv_py = os.path.realpath(REPO / ".venv" / "bin" / "python")
        ver = subprocess.run([str(REPO / ".venv" / "bin" / "python"), "-c", "import sys;print(sys.version.split()[0])"],
                             capture_output=True, text=True).stdout.strip()
        codex_native = next((L.REAL_CODEX.resolve().parents[1] / "node_modules" / "@openai").glob(
            "codex-linux-x64/vendor/*/bin/codex"), None)
        hashes = {
            "python3.10": sha256_file(py), "claude": sha256_file(os.path.realpath(L.CLAUDE_LINK)),
            "codex.js": sha256_file(L.REAL_CODEX.resolve()), "codex_native": sha256_file(codex_native) if codex_native else None,
            "node": sha256_file(Path.home() / ".local/node/bin/node"), "bwrap": sha256_file(BWRAP), "socat": sha256_file(SOCAT),
            "broker_addon": sha256_file(ADDON), "broker_policy_module": sha256_file(POLICY_MODULE),
            "broker_base_policy": sha256_file(BASE_POLICY), "entry.sh": sha256_file(ENTRY_SH), "nsexec.py": sha256_file(NSEXEC),
            "profile.agent-call/claude": agent_call_profile("claude").sha256(),
            "profile.agent-call/codex": agent_call_profile("codex").sha256(), "profile.program": program_profile().sha256(),
            "requirements.lock": sha256_file(REPO / "requirements.lock"),
        }
        versions = {"python": ver, "mitmproxy": mitmproxy_version(MITMDUMP),
                    "claude": _cli_version(["/opt/zkqec/bin/claude", "--version"], "claude", self),
                    "codex": _cli_version(["/opt/zkqec/node/bin/codex", "--version"], "codex", self)}
        self.shared["pins"] = {"hashes": hashes, "versions": versions}
        tampered = self.out / "tamper-copy"
        shutil.copy(py, tampered)
        with open(tampered, "ab") as fh:
            fh.write(b"\0")
        tamper_detected = sha256_file(tampered) != RUNTIME_DOC_SHA
        tampered.unlink()
        self.add(Property(
            "O.runtime_pinning", "Runtime, CLIs, broker and launch profiles pinned by hash", "§O/§M",
            "The archived CPython 3.10.22 matches docs/RUNTIME.md; the venv runs on it; CLI, broker (mitmproxy "
            "11.0.2) and profile hashes are recorded for the manifest; a modified binary is detected.",
            Check(hashes["python3.10"] == RUNTIME_DOC_SHA and venv_py == str(py) and ver == "3.10.22" and
                  versions["mitmproxy"] == "11.0.2" and (versions["claude"] or "").startswith("2.1.300") and
                  "0.160.1" in (versions["codex"] or ""),
                  f"python {ver} sha256 matches; versions={versions}", [{"hashes": hashes, "versions": versions}]),
            Check(tamper_detected, "a one-byte-modified copy of the interpreter fails the hash check", [])))
        lock = (REPO / "requirements.lock").read_text()
        pins, unhashed = _parse_lock(lock)
        installed = _installed_versions()
        mismatch = {k: (v, installed.get(k)) for k, v in pins.items() if k in installed and installed[k] != v}
        _, synth_unhashed = _parse_lock("foo==1.0\nbar==2.0 \\\n    --hash=sha256:" + "0" * 64 + "\n")
        self.add(Property(
            "O.lock_hashes", "Dependency closure pinned with hashes", "§O",
            "Every requirement in requirements.lock is version-pinned with sha256 hashes, and the venv matches.",
            Check(pins and not unhashed and not mismatch,
                  f"{len(pins)} pins, all hashed; installed versions match ({len(set(pins) & set(installed))} checked)",
                  [{"mismatch": mismatch}]),
            Check(synth_unhashed == ["foo"], "a synthetic lock entry without a hash is detected", [])))
        # Wheels live in the bundle CAS (zkqec.report.bundle): closures-index -> wheelhouse sidecar.
        from .report.bundle import ARCHIVE as CAS_ARCHIVE
        lock_sha = sha256_file(REPO / "requirements.lock")
        index_path = CAS_ARCHIVE / "cas" / "closures-index.json"
        index = json.loads(index_path.read_text()) if index_path.exists() else {}
        wh_sha = index.get(f"wheelhouse:{lock_sha}")
        side = json.loads((CAS_ARCHIVE / "cas" / f"{wh_sha}.json").read_text()) if wh_sha else {}
        wheels = side.get("wheels", {})
        built = {re.split(r"-\d", b, 1)[0].lower().replace("_", "-") for b in side.get("built_from_sdist", [])}
        lock_hashes = set(re.findall(r"--hash=sha256:([0-9a-f]{64})", lock))

        def _dist(wheel_name: str) -> str:
            return wheel_name.split("-")[0].lower().replace("_", "-")
        by_dist = {_dist(w): h for w, h in wheels.items()}
        covered = [k for k in pins if k.lower() in by_dist and
                   (by_dist[k.lower()] in lock_hashes or k.lower() in built)]
        tar_ok = bool(wh_sha) and sha256_file(CAS_ARCHIVE / "cas" / f"{wh_sha}.tar.zst") == wh_sha
        self.add(Property(
            "O.wheel_archive", "Locked wheels archived for offline reconstruction", "§M/§O",
            "The bundle CAS holds a content-addressed wheelhouse for this lock: every locked distribution has a "
            "wheel whose sha256 is a lock hash, or was built at seal time from its hash-checked sdist.",
            Check(bool(wheels) and len(covered) == len(pins),
                  f"{len(wheels)} wheels; {len(covered)}/{len(pins)} pins covered; built from sdist: {sorted(built)}",
                  [{"wheelhouse": wh_sha, "archive": str(CAS_ARCHIVE)}]),
            Check(tar_ok, "the wheelhouse tarball matches its content address" if tar_ok else
                  "wheelhouse missing or does not match its content address", []),
            consequence="Bundles cannot be rebuilt offline until the wheelhouse closure exists (seal a bundle "
                        "or call zkqec.report.bundle.ensure_closures)."))
        target = os.path.realpath(L.REAL_CODEX)
        wrapper_differs = L.CODEX_WRAPPER.exists() and os.path.realpath(L.CODEX_WRAPPER) != target
        argv = L.codex_call(L.CallSpec("codex", CODEX_MODEL, ROLE, PROMPT, SCHEMA)).argv
        self.add(Property(
            "O.real_codex", "Codex is the real binary, never the Claude-fallback wrapper", "§2",
            "The sandbox binds only the npm package (node launcher + native binary); ~/.local/bin/codex (a wrapper "
            "that silently falls back to Claude) is neither bound nor referenced.",
            Check(target.endswith("@openai/codex/bin/codex.js") and argv[0] == "/opt/zkqec/node/bin/codex",
                  f"real target {target}", []),
            Check(wrapper_differs and str(L.CODEX_WRAPPER) not in json.dumps(agent_call_profile("codex").to_json()),
                  f"{L.CODEX_WRAPPER} is a different file and absent from the profile", [])))

    # ==============================================================================================
    def run(self) -> dict[str, Any]:
        t0 = time.time()
        self.setup()
        try:
            for fn in (self.j0_signal_and_proc, self.j0_env_fds, self.j0_sockets, self.j0_descendants,
                       self.j1_egress, self.j1_synthetic, self.j2_canaries, self.j2_fresh_home,
                       self.live_claude, self.live_codex, self.j4_slice, self.j4_output_caps, self.j4_deadline,
                       self.o_pinning, self.j1_logs):
                self.run_property(fn)
        finally:
            self.teardown()
        return self.write_report(time.time() - t0)

    def write_report(self, seconds: float) -> dict[str, Any]:
        props = [p.to_json() for p in self.props]
        unq = [p for p in self.props if p.verdict != "qualified"]
        k0 = {
            "claude": all(p.verdict == "qualified" for p in self.props if p.pid in (
                "J1.claude_call", "J2.claude_tool_free", "J1.body_policy", "J1.host_allowlist", "J1.direct_egress",
                "K.input_bound", "K.output_ceiling", "J1.log_sanitized")),
            "codex": all(p.verdict == "qualified" for p in self.props if p.pid in ("J2.codex_tool_free",)),
        }
        dev = REPO / "runs" / "qualify" / "dev"
        supplementary = [{"log": str(lp.relative_to(REPO)), "sha256": sha256_file(lp),
                          "records": sum(1 for _ in lp.open("rb"))}
                         for lp in sorted(dev.glob("*/broker/broker-log.jsonl"))] if dev.exists() else []
        report = {"schema": "zkqec.qualify/1", "created_utc": self.out.name, "host": socket.gethostname(),
                  "supplementary_dev_evidence": supplementary,
                  "campaign": self.campaign, "seconds": round(seconds, 1), "live": self.live,
                  "spend": self.spend, "k0_enforceable": k0, "pins": self.shared.get("pins"),
                  "summary": {"qualified": len(self.props) - len(unq), "unqualified": len(unq)},
                  "unqualified": [{"id": p.pid, "consequence": p.consequence or p.note} for p in unq],
                  "properties": props}
        text = json.dumps(report, indent=2, sort_keys=False, default=str)
        (self.out / "report.json").write_text(self.redact(text))
        (self.out / "report.md").write_text(self.redact(_markdown(report)))
        return report


# --------------------------------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------------------------------
def _pid_alive(pid: int, cmd: str) -> bool:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()[:60] == cmd
    except OSError:
        return False


def _int_after(data: bytes, key: str) -> int | None:
    m = re.search(re.escape(key) + r"(\d+)", data.decode("utf-8", "replace"))
    return int(m.group(1)) if m else None


def _validate(instance: Any, schema: dict[str, Any]) -> bool:
    import jsonschema
    try:
        jsonschema.validate(instance, schema)
        return True
    except jsonschema.ValidationError:
        return False


def _caps_reject() -> bool:
    ok = True
    for bad in (b"[" * 100 + b"]" * 100, b'{"a": "' + b"x" * 5000 + b'"}', b"\xff\xfe", b"NaN"):
        try:
            parse_json_capped(bad, max_bytes=4096, max_depth=32)
            ok = False
        except OutputRejected:
            pass
    return ok and parse_json_capped(b'{"answer": 5}', max_bytes=4096) == {"answer": 5}


def _parse_lock(text: str) -> tuple[dict[str, str], list[str]]:
    pins: dict[str, str] = {}
    unhashed: list[str] = []
    blocks = re.split(r"\n(?=[A-Za-z0-9])", text)
    for b in blocks:
        m = re.match(r"([A-Za-z0-9_.\-]+)==([^\s\\;]+)", b.strip())
        if not m:
            continue
        name = m.group(1).lower().replace("_", "-")
        pins[name] = m.group(2)
        if "--hash=sha256:" not in b:
            unhashed.append(name)
    return pins, unhashed


def _pin_hashes(lock: str, name: str) -> set[str]:
    for b in re.split(r"\n(?=[A-Za-z0-9])", lock):
        m = re.match(r"([A-Za-z0-9_.\-]+)==", b.strip())
        if m and m.group(1).lower().replace("_", "-") == name:
            return set(re.findall(r"--hash=sha256:([0-9a-f]{64})", b))
    return set()


def _installed_versions() -> dict[str, str]:
    code = ("import importlib.metadata as m, json; print(json.dumps({d.metadata['Name'].lower().replace('_','-'): "
            "d.version for d in m.distributions()}))")
    out = subprocess.run([str(REPO / ".venv" / "bin" / "python"), "-c", code], capture_output=True, text=True)
    try:
        return json.loads(out.stdout)
    except ValueError:
        return {}


def _cli_version(argv: list[str], provider: str, q: Qualifier) -> str | None:
    tok = q.grant(f"ver_{provider}", provider, ["none"], Ceilings(0, 0, 0, 0, 60))
    try:
        env = {**(L.CLAUDE_ENV if provider == "claude" else L.CODEX_ENV), **L.COMMON_ENV}
        res = run_job(JobSpec(f"ver_{provider}", agent_call_profile(provider), tuple(argv), env,
                              limits=Limits(wall_seconds=60), ca_cert=str(q.broker.ca_cert), broker_port=q.broker.port,
                              token=tok), q.jobs, q.slice)
    finally:
        q.broker.revoke(tok)
    return res.stdout.decode().strip().splitlines()[-1] if res.stdout.strip() else None


def _markdown(rep: dict[str, Any]) -> str:
    lines = [f"# zkqec host qualification — {rep['created_utc']}", "",
             f"- host `{rep['host']}`, campaign `{rep['campaign']}`, {rep['seconds']} s, live={rep['live']}",
             f"- qualified **{rep['summary']['qualified']}**, unqualified **{rep['summary']['unqualified']}**",
             f"- K0 enforceable: {rep['k0_enforceable']}",
             f"- spend: {rep['spend']}", "",
             "| property | verdict | positive | negative |", "|---|---|---|---|"]
    for p in rep["properties"]:
        pos = ("PASS" if p["positive"]["passed"] else ("not run" if p["positive"]["passed"] is None else "FAIL"))
        neg = ("PASS" if p["negative"]["passed"] else ("not run" if p["negative"]["passed"] is None else "FAIL"))
        lines.append(f"| `{p['pid']}` {p['title']} | **{p['verdict']}** | {pos}: {p['positive']['summary']} | "
                     f"{neg}: {p['negative']['summary']} |".replace("\n", " "))
    lines += ["", "## Unqualified properties and consequences", ""]
    for u in rep["unqualified"]:
        lines.append(f"- `{u['id']}`: {u['consequence'] or '-'}")
    lines += ["", "## Notes", ""]
    for p in rep["properties"]:
        if p.get("note"):
            lines.append(f"- `{p['pid']}`: {p['note']}")
    lines += ["", "Evidence: `report.json` (per-check evidence), `jobs/` (captured stdio, collected outputs), "
              "`broker/broker-log.jsonl` (sanitized decisions), `synthetic/`, `deadline/`."]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="zkqec qualify-host", description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(REPO / "runs" / "qualify"), help="parent directory for the report")
    ap.add_argument("--no-live", action="store_true", help="skip live model calls")
    ap.add_argument("--no-codex", action="store_true", help="skip the live Codex call")
    ap.add_argument("--archive", default=str(DEFAULT_ARCHIVE), help="wheel archive to verify")
    args = ap.parse_args(argv)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = Path(args.out).resolve() / stamp
    q = Qualifier(out, live=not args.no_live, codex_live=not args.no_codex, archive=Path(args.archive))
    rep = q.run()
    print(f"[qualify] report: {out / 'report.md'}")
    print(f"[qualify] qualified={rep['summary']['qualified']} unqualified={rep['summary']['unqualified']} "
          f"k0={rep['k0_enforceable']} spend={rep['spend']}")
    for u in rep["unqualified"]:
        print(f"[qualify] UNQUALIFIED {u['id']}")
    return 0 if not rep["unqualified"] else 2


if __name__ == "__main__":
    sys.exit(main())
