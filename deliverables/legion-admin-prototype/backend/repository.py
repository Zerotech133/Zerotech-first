"""Read-only adapters for GenOS registry and WATCH_TOWER HR records."""
from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple


class RegistryDataError(RuntimeError):
    """Raised when a source is unavailable or invalid."""


@dataclass(frozen=True)
class SourceMeta:
    source: str
    source_mtime: Optional[str]
    observed_at: str
    quality: str = "live"


@dataclass(frozen=True)
class HealthDto:
    agent_id: str
    state: str
    endpoint: Optional[str]
    reachable: Optional[bool]
    latency_ms: Optional[float]
    checked_at: Optional[str]
    detail: Optional[str]
    error: Optional[Mapping[str, str]]
    source: str
    source_mtime: Optional[str]
    observed_at: str
    quality: str


@dataclass(frozen=True)
class AgentDto:
    agent_id: str
    name: str
    title: str
    kind: str
    dept_id: Optional[str]
    dept_name: Optional[str]
    rank: Optional[str]
    supervisor_id: Optional[str]
    endpoint: Optional[str]
    admin_status: str
    live_state: str
    identity: Mapping[str, Any]
    permissions: Mapping[str, Any]
    employment: Mapping[str, Any]
    runtime: Mapping[str, Any]
    availability: Mapping[str, Any]
    source: Mapping[str, Any]
    appointment: Mapping[str, Any]
    resume: Mapping[str, Any]


class RegistryReader:
    """Read source files and perform non-invasive health probes."""

    def __init__(self, org_path: Path | str = Path(r"C:\aios\agent_platform\registry\org.json"), hr_root: Path | str = Path(r"C:\aios\agent_platform\hr"), timeout: float = 0.8) -> None:
        self.org_path = Path(org_path).resolve()
        self.hr_root = Path(hr_root).resolve()
        self.timeout = max(0.05, float(timeout))
        self.root = self.org_path.parent.parent.resolve()
        if not self.org_path.is_relative_to(self.root) or not self.hr_root.is_relative_to(self.root):
            raise RegistryDataError("Source paths must remain inside agent platform root")

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _mtime(path: Path) -> Optional[str]:
        try:
            return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            return None

    def _load_org(self) -> Mapping[str, Any]:
        try:
            value = json.loads(self.org_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryDataError(f"Unable to load organization registry: {self.org_path}") from exc
        if not isinstance(value, dict):
            raise RegistryDataError("Organization registry must be a JSON object")
        return value

    @staticmethod
    def _yaml(path: Path) -> Mapping[str, Any]:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RegistryDataError("PyYAML is required to parse HR YAML; install requirements.txt") from exc
        try:
            value = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RegistryDataError(f"Unable to load HR record: {path}") from exc
        except Exception as exc:
            # PyYAML ScannerError/ParserError 等均归一化为可观测的数据源错误。
            raise RegistryDataError(f"Invalid HR YAML: {path}") from exc
        if not isinstance(value, dict):
            raise RegistryDataError(f"HR record must be a YAML mapping: {path}")
        return value

    def _load_hr(self, agent_id: str) -> Tuple[Mapping[str, Any], Mapping[str, Any]]:
        directory = (self.hr_root / agent_id).resolve()
        if not directory.is_relative_to(self.hr_root):
            raise RegistryDataError("Invalid HR path")
        return self._yaml(directory / "resume.yaml"), self._yaml(directory / "appointment.yaml")

    @staticmethod
    def _profiles(org: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
        found: Dict[str, Mapping[str, Any]] = {}
        chief = org.get("chief")
        if isinstance(chief, dict) and chief.get("agent_id"):
            found[str(chief["agent_id"])] = chief
        for department in org.get("departments", []):
            if not isinstance(department, dict):
                continue
            for profile in department.get("agent_profiles", []):
                if isinstance(profile, dict) and profile.get("agent_id"):
                    found[str(profile["agent_id"])] = profile
            for member in department.get("agents", []):
                member_id = str(member.get("agent_id")) if isinstance(member, dict) else str(member)
                found.setdefault(member_id, {"agent_id": member_id})
        return found

    @staticmethod
    def _department(agent_id: str, org: Mapping[str, Any]) -> Optional[Mapping[str, Any]]:
        for department in org.get("departments", []):
            if isinstance(department, dict):
                members = [str(x.get("agent_id")) if isinstance(x, dict) else str(x) for x in department.get("agents", [])]
                if agent_id in members:
                    return department
        return None

    def _known(self, agent_id: str, org: Mapping[str, Any]) -> bool:
        return agent_id in self._profiles(org)

    def _probe(self, agent_id: str, endpoint: Optional[str], source_mtime: Optional[str]) -> Dict[str, Any]:
        """Probe HTTP health when endpoint is explicitly HTTP; otherwise TCP degraded fallback."""
        observed = self._now()
        if not endpoint:
            return asdict(HealthDto(agent_id, "unknown", None, None, None, None, "No endpoint registered", {"kind": "missing_endpoint", "message": "Agent has no endpoint"}, "registry_only", source_mtime, observed, "registry_only"))
        raw_endpoint = endpoint if "://" in endpoint else f"tcp://{endpoint}"
        if raw_endpoint.startswith(("http://", "https://")):
            health_url = raw_endpoint.rstrip("/") + "/health"
            started = time.perf_counter()
            try:
                request = urllib.request.Request(health_url, headers={"Accept": "application/json"}, method="GET")
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read(65536)
                    latency = round((time.perf_counter() - started) * 1000, 2)
                    if not 200 <= response.status < 300:
                        raise urllib.error.HTTPError(health_url, response.status, "non-2xx health response", response.headers, None)
                    try:
                        payload = json.loads(body.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        return asdict(HealthDto(agent_id, "offline", endpoint, False, latency, observed, "Health response is not JSON", {"kind": "invalid_payload", "message": str(exc)}, "agent_health", source_mtime, observed, "probe_failed"))
                    if not isinstance(payload, dict):
                        return asdict(HealthDto(agent_id, "offline", endpoint, False, latency, observed, "Health response must be an object", {"kind": "invalid_payload", "message": "Expected JSON object"}, "agent_health", source_mtime, observed, "probe_failed"))
                    return asdict(HealthDto(agent_id, "online", endpoint, True, latency, observed, "HTTP health endpoint returned 2xx JSON", None, "agent_health", source_mtime, observed, "probe_ok"))
            except urllib.error.HTTPError as exc:
                error = {"kind": "invalid_payload" if exc.code < 200 or exc.code >= 300 else "connection_refused", "message": f"HTTP health status {exc.code}"}
            except urllib.error.URLError as exc:
                reason = str(exc.reason)
                error = {"kind": "timeout" if "timed out" in reason.lower() else "connection_refused", "message": reason}
            except TimeoutError:
                error = {"kind": "timeout", "message": f"Probe exceeded {self.timeout:.3f}s"}
            return asdict(HealthDto(agent_id, "offline", endpoint, False, None, observed, "HTTP health endpoint unavailable", error, "agent_health", source_mtime, observed, "probe_failed"))
        host, separator, port_text = endpoint.rpartition(":")
        if not separator or not host or not port_text.isdigit():
            return asdict(HealthDto(agent_id, "offline", endpoint, False, None, observed, "Invalid endpoint", {"kind": "invalid_payload", "message": "Endpoint must be host:port or http(s)://host:port"}, "agent_health", source_mtime, observed, "probe_failed"))
        started = time.perf_counter()
        try:
            with socket.create_connection((host, int(port_text)), timeout=self.timeout):
                latency = round((time.perf_counter() - started) * 1000, 2)
                return asdict(HealthDto(agent_id, "degraded", endpoint, True, latency, observed, "TCP endpoint reachable; HTTP health protocol not declared", {"kind": "protocol_unknown", "message": "TCP fallback did not validate /health"}, "agent_health", source_mtime, observed, "endpoint_reachable"))
        except socket.timeout:
            error = {"kind": "timeout", "message": f"Probe exceeded {self.timeout:.3f}s"}
        except ConnectionRefusedError:
            error = {"kind": "connection_refused", "message": "Endpoint refused connection"}
        except OSError as exc:
            error = {"kind": "connection_refused", "message": str(exc)}
        return asdict(HealthDto(agent_id, "offline", endpoint, False, None, observed, "Endpoint unavailable", error, "agent_health", source_mtime, observed, "probe_failed"))

    def health(self, agent_id: str) -> Dict[str, Any]:
        detail = self.agent_detail(agent_id, probe=False)
        return self._probe(agent_id, detail["endpoint"], detail["source"].get("source_mtime"))

    def _agent(self, agent_id: str, org: Mapping[str, Any], probe: bool) -> Dict[str, Any]:
        resume, appointment = self._load_hr(agent_id)
        profile = self._profiles(org).get(agent_id, {})
        department = self._department(agent_id, org) or {}
        files = [self.org_path, self.hr_root / agent_id / "resume.yaml", self.hr_root / agent_id / "appointment.yaml"]
        mtimes = {str(path): self._mtime(path) for path in files}
        source = {"registry": str(self.org_path), "hr": [str(files[1]), str(files[2])], "source_mtime": mtimes, "observed_at": self._now()}
        endpoint = str(appointment["endpoint"]) if appointment.get("endpoint") else None
        runtime = self._probe(agent_id, endpoint, mtimes.get(str(files[2]))) if probe else {"state": "unknown", "endpoint": endpoint, "source": "registry_only"}
        tools = appointment.get("tools_allowed") if isinstance(appointment.get("tools_allowed"), dict) else {"read": [], "write": [], "execute": [], "danger": "deny"}
        return asdict(AgentDto(agent_id, str(resume.get("name") or profile.get("name") or agent_id), str(appointment.get("title") or agent_id), str(resume.get("kind") or "agent"), str(appointment.get("dept") or department.get("dept")) if (appointment.get("dept") or department.get("dept")) else None, str(department.get("name")) if department.get("name") else None, str(appointment.get("rank")) if appointment.get("rank") else None, str(appointment.get("supervisor")) if appointment.get("supervisor") else None, endpoint, str(profile.get("status") or "active"), runtime.get("state", "unknown"), {"origin": resume.get("identity", {}).get("origin") if isinstance(resume.get("identity"), dict) else None, "model": resume.get("identity", {}).get("model") if isinstance(resume.get("identity"), dict) else None, "born": resume.get("identity", {}).get("born") if isinstance(resume.get("identity"), dict) else None, "skills": resume.get("skills", []), "self_statement": resume.get("self_statement")}, tools, {"onboarded": appointment.get("onboarded"), "approvals": appointment.get("approvals", {})}, runtime, {"identity": True, "health": runtime.get("state") != "unknown", "metrics": False, "reason": "No P0 metrics source"}, source, appointment, resume))

    def agent_detail(self, agent_id: str, probe: bool = True) -> Dict[str, Any]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", agent_id):
            raise ValueError("Invalid agent_id")
        org = self._load_org()
        if not self._known(agent_id, org):
            raise KeyError(agent_id)
        return self._agent(agent_id, org, probe)

    def _partial_agent(self, agent_id: str, department: Mapping[str, Any], error: Exception) -> Dict[str, Any]:
        """Build an observable unknown node when one member HR source is bad."""
        profile = self._profiles(self._load_org()).get(agent_id, {})
        source = {
            "registry": str(self.org_path),
            "hr": str(self.hr_root / agent_id),
            "source_mtime": {str(self.org_path): self._mtime(self.org_path)},
            "observed_at": self._now(),
            "quality": "partial",
            "error": {"kind": "source_invalid", "message": str(error)},
        }
        return {
            "node_id": agent_id,
            "type": "agent",
            "parent_id": str(department.get("dept", "")),
            "label": str(profile.get("name") or agent_id),
            "agent_id": agent_id,
            "dept_id": str(department.get("dept", "")),
            "admin_status": str(profile.get("status") or "unknown"),
            "live_state": "unknown",
            "source": source,
            "children": [],
        }

    def organization_tree(self, include_health: bool = False, query: Optional[str] = None) -> Dict[str, Any]:
        org = self._load_org()
        source = {"registry": str(self.org_path), "source_mtime": self._mtime(self.org_path), "observed_at": self._now(), "quality": "live"}
        children: List[Dict[str, Any]] = []
        partial_errors: List[Dict[str, str]] = []
        for department in org.get("departments", []):
            if not isinstance(department, dict):
                continue
            dept_id = str(department.get("dept", ""))
            agents: List[Dict[str, Any]] = []
            for member in department.get("agents", []):
                agent_id = str(member.get("agent_id")) if isinstance(member, dict) else str(member)
                if query and query.lower() not in agent_id.lower() and query.lower() not in str(department.get("name", "")).lower():
                    continue
                try:
                    item = self.agent_detail(agent_id, probe=include_health)
                    node = {"node_id": agent_id, "type": "agent", "parent_id": dept_id, "label": item["name"], "agent_id": agent_id, "dept_id": dept_id, "admin_status": item["admin_status"], "live_state": item["live_state"], "source": item["source"], "children": []}
                except (RegistryDataError, ValueError) as error:
                    node = self._partial_agent(agent_id, department, error)
                    partial_errors.append({"agent_id": agent_id, "message": str(error)})
                agents.append(node)
            children.append({"node_id": dept_id, "type": "department", "parent_id": str(org.get("org", "root")), "label": department.get("name", dept_id), "dept_id": dept_id, "admin_status": department.get("status", "unknown"), "live_state": "unknown", "count": len(agents), "lead_agent_id": department.get("lead"), "source": source, "children": agents})
        if partial_errors:
            source = dict(source)
            source["quality"] = "partial"
            source["errors"] = partial_errors
        return {"node_id": str(org.get("org", "root")), "type": "root", "parent_id": None, "label": org.get("host", org.get("org", "GenOS")), "admin_status": "active", "live_state": "unknown", "children": children, "source": source}
