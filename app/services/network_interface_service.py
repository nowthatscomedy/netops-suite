from __future__ import annotations

import logging
import subprocess

from app.models.network_models import NetworkAdapterInfo
from app.models.profile_models import IPProfile
from app.models.result_models import OperationResult
from app.services.powershell_service import PowerShellService
from app.utils.process_utils import no_window_creationflags, windows_console_encoding
from app.utils.validators import prefix_to_netmask


class NetworkInterfaceService:
    def __init__(self, powershell: PowerShellService, logger: logging.Logger) -> None:
        self.powershell = powershell
        self.logger = logger

    def list_adapters(self) -> list[NetworkAdapterInfo]:
        script = """
$adapters = Get-NetAdapter | Sort-Object Name | ForEach-Object {
  $alias = $_.Name
  $ipEntries = @(
    Get-NetIPAddress -InterfaceAlias $alias -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -and $_.IPAddress -notlike '127.*' } |
    Sort-Object `
      @{ Expression = { if ($_.PrefixOrigin -eq 'Manual') { 0 } elseif ($_.IPAddress -like '169.254.*') { 2 } else { 1 } } }, `
      @{ Expression = { $_.SkipAsSource } }
  )
  $selectedIpv4 = $ipEntries | Select-Object -First 1
  $gateway = Get-NetRoute -InterfaceAlias $alias -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
    Sort-Object RouteMetric |
    Select-Object -First 1
  $dnsInfo = Get-DnsClientServerAddress -InterfaceAlias $alias -AddressFamily IPv4 -ErrorAction SilentlyContinue
  $dhcpInfo = Get-NetIPInterface -InterfaceAlias $alias -AddressFamily IPv4 -ErrorAction SilentlyContinue
  $hasManualIpv4 = @($ipEntries | Where-Object { $_.PrefixOrigin -eq 'Manual' }).Count -gt 0

  [PSCustomObject]@{
    Name = $_.Name
    InterfaceDescription = $_.InterfaceDescription
    MacAddress = $_.MacAddress
    Status = $_.Status.ToString()
    LinkSpeed = $_.LinkSpeed
    InterfaceIndex = $_.IfIndex
    IPv4 = if ($selectedIpv4) { $selectedIpv4.IPAddress } else { '' }
    PrefixLength = if ($selectedIpv4) { $selectedIpv4.PrefixLength } else { 0 }
    Gateway = if ($gateway) { $gateway.NextHop } else { '' }
    DNS = if ($dnsInfo -and $dnsInfo.ServerAddresses) { @($dnsInfo.ServerAddresses) } else { @() }
    DhcpEnabled = if ($hasManualIpv4) { $false } elseif ($dhcpInfo) { $dhcpInfo.Dhcp -eq 'Enabled' } else { $false }
    InterfaceType = $_.InterfaceType.ToString()
  }
}
$adapters | ConvertTo-Json -Depth 5 -Compress
"""
        data = self.powershell.run_json(script, timeout=20)
        if not data:
            return []
        if isinstance(data, dict):
            data = [data]
        adapters = [NetworkAdapterInfo.from_dict(item) for item in data]
        self.logger.info("Loaded %s network adapters.", len(adapters))
        return adapters

    def set_dhcp(self, interface_name: str) -> OperationResult:
        interface_ref = self._netsh_interface_ref(interface_name)
        address_result = self._run_netsh(
            ["netsh", "interface", "ipv4", "set", "address", interface_ref, "source=dhcp"]
        )
        dns_result = self._run_netsh(
            ["netsh", "interface", "ipv4", "set", "dnsservers", interface_ref, "source=dhcp"]
        )
        cleanup_result = self._cleanup_after_dhcp(interface_name)
        renew_result = self._renew_dhcp_lease(interface_name)

        if address_result.success and dns_result.success:
            details = "\n\n".join(filter(None, [cleanup_result.details, renew_result.details]))
            return OperationResult(True, self._dhcp_apply_message(interface_name, renew_result), details)

        alias = self.powershell.quote(interface_name)
        script = f"""
$alias = {alias}
Get-NetIPAddress -InterfaceAlias $alias -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Where-Object {{ $_.PrefixOrigin -eq 'Manual' }} |
  Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
Set-NetIPInterface -InterfaceAlias $alias -AddressFamily IPv4 -Dhcp Enabled -ErrorAction SilentlyContinue | Out-Null
Set-DnsClientServerAddress -InterfaceAlias $alias -ResetServerAddresses -ErrorAction SilentlyContinue | Out-Null
"""
        fallback = self.powershell.run(script, timeout=30)
        if fallback.success:
            fallback_renew = self._renew_dhcp_lease(interface_name)
            details = "\n\n".join(filter(None, [cleanup_result.details, fallback_renew.details]))
            return OperationResult(True, self._dhcp_apply_message(interface_name, fallback_renew), details)

        return OperationResult(
            False,
            f"{interface_name}에 DHCP를 적용하지 못했습니다.",
            "\n\n".join(
                filter(
                    None,
                    [
                        address_result.details,
                        dns_result.details,
                        cleanup_result.details,
                        renew_result.details,
                        fallback.stderr,
                    ],
                )
            ),
        )

    def set_static(
        self,
        interface_name: str,
        local_ip: str,
        prefix: int,
        gateway: str = "",
        dns_servers: list[str] | None = None,
    ) -> OperationResult:
        dns_servers = dns_servers or []
        netsh_result = self._netsh_set_static(interface_name, local_ip, prefix, gateway, dns_servers)
        if netsh_result.success:
            cleanup_result = self._cleanup_after_static(interface_name, local_ip)
            details = "\n\n".join(filter(None, [cleanup_result.details]))
            return OperationResult(True, f"{interface_name}에 고정 IP {local_ip}/{prefix}를 적용했습니다.", details)

        alias = self.powershell.quote(interface_name)
        gateway_route_clause = (
            f"New-NetRoute -InterfaceAlias $alias -DestinationPrefix '0.0.0.0/0' -NextHop {self.powershell.quote(gateway)} -RouteMetric 1 -ErrorAction SilentlyContinue | Out-Null"
            if gateway
            else ""
        )
        dns_clause = (
            f"Set-DnsClientServerAddress -InterfaceAlias $alias -ServerAddresses @({', '.join(self.powershell.quote(item) for item in dns_servers)}) -ErrorAction Stop"
            if dns_servers
            else "Set-DnsClientServerAddress -InterfaceAlias $alias -ResetServerAddresses -ErrorAction Stop"
        )
        script = f"""
$alias = {alias}
$targetIp = {self.powershell.quote(local_ip)}
try {{
  Set-NetIPInterface -InterfaceAlias $alias -AddressFamily IPv4 -Dhcp Disabled -ErrorAction Stop | Out-Null
}} catch {{
}}
Get-NetIPAddress -InterfaceAlias $alias -AddressFamily IPv4 -ErrorAction SilentlyContinue |
  Where-Object {{ $_.IPAddress -ne $targetIp }} |
  Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
$params = @{{
  InterfaceAlias = $alias
  IPAddress = $targetIp
  PrefixLength = {int(prefix)}
  AddressFamily = 'IPv4'
  ErrorAction = 'Stop'
}}
$existingIp = Get-NetIPAddress -InterfaceAlias $alias -AddressFamily IPv4 -IPAddress $targetIp -ErrorAction SilentlyContinue |
  Select-Object -First 1
if (-not $existingIp -or [int]$existingIp.PrefixLength -ne {int(prefix)}) {{
  if ($existingIp) {{
    $existingIp | Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
  }}
  New-NetIPAddress @params | Out-Null
}}
Get-NetRoute -InterfaceAlias $alias -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
  Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
{gateway_route_clause}
{dns_clause}
"""
        fallback = self.powershell.run(script, timeout=30)
        if fallback.success:
            cleanup_fallback = self._cleanup_after_static(interface_name, local_ip)
            details = "\n\n".join(filter(None, [cleanup_fallback.details]))
            return OperationResult(True, f"{interface_name}에 고정 IP {local_ip}/{prefix}를 적용했습니다.", details)

        return OperationResult(
            False,
            f"{interface_name}에 고정 IP를 적용하지 못했습니다.",
            "\n\n".join(filter(None, [netsh_result.details, fallback.stderr])),
        )

    def set_dns(self, interface_name: str, dns_servers: list[str]) -> OperationResult:
        alias = self.powershell.quote(interface_name)
        if dns_servers:
            script = (
                f"Set-DnsClientServerAddress -InterfaceAlias {alias} -ServerAddresses "
                f"@({', '.join(self.powershell.quote(item) for item in dns_servers)}) -ErrorAction Stop"
            )
            success_message = f"{interface_name} DNS 서버를 변경했습니다."
        else:
            script = f"Set-DnsClientServerAddress -InterfaceAlias {alias} -ResetServerAddresses -ErrorAction Stop"
            success_message = f"{interface_name} DNS 서버를 자동으로 전환했습니다."
        result = self.powershell.run(script, timeout=20)
        if result.success:
            return OperationResult(True, success_message, result.stdout)
        return OperationResult(False, f"{interface_name} DNS 서버 변경에 실패했습니다.", result.stderr)

    def apply_profile(self, interface_name: str, profile: IPProfile) -> OperationResult:
        if profile.mode.lower() == "dhcp":
            return self.set_dhcp(interface_name)
        return self.set_static(interface_name, profile.local_ip, profile.prefix, profile.gateway, profile.dns)

    def format_adapter_snapshot(self, adapters: list[NetworkAdapterInfo]) -> str:
        if not adapters:
            return "네트워크 인터페이스를 찾지 못했습니다."

        blocks: list[str] = []
        for adapter in adapters:
            prefix_text = (
                f"{adapter.prefix_length} / {prefix_to_netmask(adapter.prefix_length)}"
                if adapter.prefix_length
                else "-"
            )
            blocks.append(
                "\n".join(
                    [
                        f"이름      : {adapter.name}",
                        f"설명      : {adapter.interface_description}",
                        f"MAC       : {adapter.mac_address}",
                        f"상태      : {adapter.status}",
                        f"DHCP      : {'사용' if adapter.dhcp_enabled else '사용 안 함'}",
                        f"IPv4      : {adapter.ipv4 or '-'}",
                        f"Prefix    : {prefix_text}",
                        f"Gateway   : {adapter.gateway or '-'}",
                        f"DNS       : {adapter.dns_text() or '-'}",
                        f"링크 속도 : {adapter.link_speed or '-'}",
                    ]
                )
            )
        return "\n\n".join(blocks)

    def _netsh_set_static(
        self,
        interface_name: str,
        local_ip: str,
        prefix: int,
        gateway: str,
        dns_servers: list[str],
    ) -> OperationResult:
        interface_ref = self._netsh_interface_ref(interface_name)
        netmask = prefix_to_netmask(prefix)

        address_command = [
            "netsh",
            "interface",
            "ipv4",
            "set",
            "address",
            interface_ref,
            "static",
            local_ip,
            netmask,
        ]
        if gateway:
            address_command.extend([gateway, "1"])
        else:
            address_command.append("none")

        address_result = self._run_netsh(address_command)
        if not address_result.success:
            return OperationResult(False, "netsh 고정 IP 적용 실패", address_result.details)

        if dns_servers:
            primary_dns = self._run_netsh(
                [
                    "netsh",
                    "interface",
                    "ipv4",
                    "set",
                    "dnsservers",
                    interface_ref,
                    "static",
                    dns_servers[0],
                    "primary",
                    "validate=no",
                ]
            )
            if not primary_dns.success:
                return OperationResult(False, "netsh DNS 적용 실패", primary_dns.details)

            for index, dns_server in enumerate(dns_servers[1:], start=2):
                self._run_netsh(
                    [
                        "netsh",
                        "interface",
                        "ipv4",
                        "add",
                        "dnsservers",
                        interface_ref,
                        dns_server,
                        f"index={index}",
                        "validate=no",
                    ]
                )
        else:
            clear_dns = self._run_netsh(
                [
                    "netsh",
                    "interface",
                    "ipv4",
                    "set",
                    "dnsservers",
                    interface_ref,
                    "source=static",
                    "address=none",
                    "validate=no",
                ]
            )
            if not clear_dns.success:
                return OperationResult(False, "netsh DNS 초기화 실패", clear_dns.details)

        return OperationResult(True, "netsh 고정 IP 적용 성공", address_result.details)

    def _cleanup_after_dhcp(self, interface_name: str) -> OperationResult:
        alias = self.powershell.quote(interface_name)
        script = f"""
$alias = {alias}
try {{
  Get-NetIPAddress -InterfaceAlias $alias -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {{ $_.PrefixOrigin -eq 'Manual' }} |
    Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
  Set-NetIPInterface -InterfaceAlias $alias -AddressFamily IPv4 -Dhcp Enabled -ErrorAction SilentlyContinue | Out-Null
  Set-DnsClientServerAddress -InterfaceAlias $alias -ResetServerAddresses -ErrorAction SilentlyContinue | Out-Null
  Get-NetRoute -InterfaceAlias $alias -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
    Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
}} catch {{
}}
"""
        result = self.powershell.run(script, timeout=20)
        if result.success:
            return OperationResult(True, "DHCP 후처리 완료")
        return OperationResult(False, "DHCP 후처리 실패", result.stderr or result.stdout)

    def _renew_dhcp_lease(self, interface_name: str) -> OperationResult:
        alias = self.powershell.quote(interface_name)
        script = f"""
$alias = {alias}
$adapter = Get-NetAdapter -InterfaceAlias $alias -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $adapter) {{
  Write-Output "어댑터를 찾지 못해 DHCP lease 갱신을 건너뜁니다."
  return
}}

$netConfig = Get-CimInstance Win32_NetworkAdapterConfiguration -Filter "IPEnabled = True" -ErrorAction SilentlyContinue |
  Where-Object {{ $_.InterfaceIndex -eq $adapter.IfIndex -or $_.Description -eq $adapter.InterfaceDescription }} |
  Select-Object -First 1
if ($netConfig) {{
  Invoke-CimMethod -InputObject $netConfig -MethodName ReleaseDHCPLease -ErrorAction SilentlyContinue | Out-Null
  Start-Sleep -Milliseconds 500
  Invoke-CimMethod -InputObject $netConfig -MethodName RenewDHCPLease -ErrorAction SilentlyContinue | Out-Null
}}

$deadline = (Get-Date).AddSeconds(20)
$lease = $null
do {{
  Start-Sleep -Milliseconds 500
  $lease = Get-NetIPAddress -InterfaceAlias $alias -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {{ $_.IPAddress -and $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' }} |
    Sort-Object @{{ Expression = {{ if ($_.PrefixOrigin -eq 'Dhcp') {{ 0 }} else {{ 1 }} }} }} |
    Select-Object -First 1
  if ($lease) {{ break }}
}} while ((Get-Date) -lt $deadline)

if ($lease) {{
  "DHCP lease 갱신 완료: $($lease.IPAddress)/$($lease.PrefixLength)"
}} else {{
  "DHCP는 활성화됐지만 아직 정상 IPv4 lease를 받지 못했습니다. 케이블, VLAN, DHCP 서버 상태를 확인해 주세요."
}}
"""
        result = self.powershell.run(script, timeout=30)
        if result.success:
            details = result.stdout.strip()
            lease_acquired = "DHCP lease 갱신 완료:" in details
            message = "DHCP lease 갱신 완료" if lease_acquired else "DHCP lease 미수신"
            return OperationResult(True, message, details, {"lease_acquired": lease_acquired})
        return OperationResult(False, "DHCP lease 갱신 실패", result.stderr or result.stdout)

    def _dhcp_apply_message(self, interface_name: str, renew_result: OperationResult) -> str:
        if isinstance(renew_result.payload, dict) and not renew_result.payload.get("lease_acquired", True):
            return f"{interface_name}에 DHCP를 적용했지만 정상 IPv4 lease를 아직 받지 못했습니다."
        return f"{interface_name}에 DHCP를 적용했습니다."

    def _cleanup_after_static(self, interface_name: str, target_ip: str) -> OperationResult:
        alias = self.powershell.quote(interface_name)
        target_ip_quoted = self.powershell.quote(target_ip)
        script = f"""
$alias = {alias}
$targetIp = {target_ip_quoted}
try {{
  Set-NetIPInterface -InterfaceAlias $alias -AddressFamily IPv4 -Dhcp Disabled -ErrorAction SilentlyContinue | Out-Null
  Get-NetIPAddress -InterfaceAlias $alias -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object {{ $_.IPAddress -ne $targetIp }} |
    Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
}} catch {{
}}
"""
        result = self.powershell.run(script, timeout=20)
        if result.success:
            return OperationResult(True, "고정 IP 후처리 완료")
        return OperationResult(False, "고정 IP 후처리 실패", result.stderr or result.stdout)

    def _run_netsh(self, command: list[str]) -> OperationResult:
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding=windows_console_encoding(),
                errors="replace",
                timeout=30,
                creationflags=no_window_creationflags(),
            )
        except subprocess.TimeoutExpired as exc:
            details = (exc.stderr or exc.stdout or "netsh 명령이 30초 안에 완료되지 않았습니다.")
            return OperationResult(False, "netsh 명령 시간이 초과되었습니다.", str(details))
        details = completed.stderr or completed.stdout
        if completed.returncode == 0:
            return OperationResult(True, "netsh 명령이 완료되었습니다.", details)
        return OperationResult(False, "netsh 명령 실행에 실패했습니다.", details)

    def _netsh_interface_ref(self, interface_name: str) -> str:
        alias = self.powershell.quote(interface_name)
        script = f"""
$adapter = Get-NetAdapter -InterfaceAlias {alias} -ErrorAction SilentlyContinue | Select-Object -First 1
if ($adapter) {{
  $adapter.IfIndex | ConvertTo-Json -Compress
}}
"""
        try:
            data = self.powershell.run_json(script, timeout=10)
        except Exception:
            data = None
        if data is not None and str(data).strip():
            return f"name={str(data).strip()}"
        return f'name="{interface_name}"'
