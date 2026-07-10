from __future__ import annotations

import hashlib
import json
from typing import Any

from app.assistant.models import ToolCallRequest


ACTION_TOOL_MAP = {
    "ping": "net.ping",
    "external_ping": "net.external_ping",
    "tcp_check": "net.tcp_check",
    "subnet_calculate": "net.subnet.calculate",
    "dns_lookup": "net.dns.lookup",
    "dns_flush_cache": "net.dns.flush_cache",
    "public_ip": "net.public_ip",
    "ipconfig": "net.ipconfig.read",
    "route_print": "net.route.print",
    "arp_table": "net.arp.table",
    "interface_snapshot": "net.interface.snapshot",
    "set_dns": "net.interface.set_dns",
    "set_dhcp": "net.interface.set_dhcp",
    "set_static_ip": "net.interface.set_static_ip",
    "wireless_status": "wifi.status",
    "wireless_scan": "wifi.scan_nearby",
    "oui_lookup": "oui.lookup",
    "oui_cache_refresh": "oui.cache.refresh",
}


def tool_call_from_netops_action(action: Any, *, user_intent: str = "", session_id: str = "") -> ToolCallRequest:
    tool_name = ACTION_TOOL_MAP.get(str(getattr(action, "kind", "")))
    if not tool_name:
        raise ValueError(f"지원하지 않는 NetOps 어시스턴트 작업입니다: {getattr(action, 'kind', '')}")

    args: dict[str, Any] = {}
    kind = getattr(action, "kind", "")
    if kind == "ping":
        args["target"] = getattr(action, "target", "")
    if kind == "external_ping":
        targets = tuple(getattr(action, "targets", ()) or ())
        if targets:
            args["targets"] = list(targets)
    if kind == "tcp_check":
        args.update({"target": getattr(action, "target", ""), "port": int(getattr(action, "port", 0) or 0)})
    if kind == "dns_lookup":
        args.update(
            {
                "query": getattr(action, "target", ""),
                "record_type": getattr(action, "record_type", "A") or "A",
                "server": getattr(action, "server", "") or "",
            }
        )
    if kind == "subnet_calculate":
        args["cidr"] = getattr(action, "target", "")
    if kind == "set_dns":
        args.update(
            {
                "interface_name": getattr(action, "interface_name", ""),
                "dns_servers": list(getattr(action, "dns_servers", ()) or ()),
            }
        )
    if kind == "set_dhcp":
        args["interface_name"] = getattr(action, "interface_name", "")
    if kind == "set_static_ip":
        args.update(
            {
                "interface_name": getattr(action, "interface_name", ""),
                "ip_address": getattr(action, "ip_address", ""),
                "prefix": int(getattr(action, "prefix", 0) or 0),
                "gateway": getattr(action, "gateway", "") or "",
                "dns_servers": list(getattr(action, "dns_servers", ()) or ()),
            }
        )
    if kind == "oui_lookup":
        args["mac_address"] = getattr(action, "target", "")
    if kind == "wireless_scan":
        args.update(
            {
                "duration_seconds": int(getattr(action, "duration_seconds", 20) or 20),
                "interval_seconds": int(getattr(action, "interval_seconds", 5) or 5),
            }
        )

    identity = json.dumps({"tool": tool_name, "args": args, "intent": user_intent}, ensure_ascii=False, sort_keys=True)
    call_id = f"{tool_name}:{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:16]}"
    return ToolCallRequest(
        tool_name=tool_name,
        arguments=args,
        call_id=call_id,
        actor="netops_assistant",
        metadata={"source": "netops_assistant", "session_id": session_id, "user_intent": user_intent},
    )
