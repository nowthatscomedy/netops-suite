from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor as RealThreadPoolExecutor

from app.models.result_models import PingResult, TcpCheckResult
from app.services import ping_service as ping_service_module
from app.services import tcp_check_service as tcp_service_module
from app.services.ping_service import PingService
from app.services.tcp_check_service import TcpCheckService


def test_ping_auto_workers_start_every_input_target(monkeypatch):
    worker_counts: list[int] = []

    class RecordingExecutor(RealThreadPoolExecutor):
        def __init__(self, max_workers: int, *args, **kwargs) -> None:
            worker_counts.append(max_workers)
            super().__init__(max_workers=max_workers, *args, **kwargs)

    service = PingService(logging.getLogger("test-ping-auto-workers"))
    monkeypatch.setattr(ping_service_module, "ThreadPoolExecutor", RecordingExecutor)
    monkeypatch.setattr(
        service,
        "_ping_target",
        lambda name, target, *_args: PingResult(
            name=name,
            target=target,
            success=True,
            status="정상",
            packet_loss=0,
        ),
    )

    results = service.run_multi_ping(
        "GW,192.168.0.1\nDNS,8.8.8.8\n192.168.0.254",
        count=4,
        timeout_ms=4000,
        continuous=True,
    )

    assert worker_counts == [3]
    assert [(result.name, result.target) for result in results] == [
        ("192.168.0.254", "192.168.0.254"),
        ("DNS", "8.8.8.8"),
        ("GW", "192.168.0.1"),
    ]


def test_tcp_auto_workers_start_every_target_port_combination(monkeypatch):
    worker_counts: list[int] = []

    class RecordingExecutor(RealThreadPoolExecutor):
        def __init__(self, max_workers: int, *args, **kwargs) -> None:
            worker_counts.append(max_workers)
            super().__init__(max_workers=max_workers, *args, **kwargs)

    service = TcpCheckService(logging.getLogger("test-tcp-auto-workers"))
    monkeypatch.setattr(tcp_service_module, "ThreadPoolExecutor", RecordingExecutor)
    monkeypatch.setattr(
        service,
        "_run_single_check",
        lambda name, target, port, *_args: TcpCheckResult(
            name=name,
            target=target,
            port=port,
            status="열림",
        ),
    )

    results = service.run_multi_check(
        "API,10.0.0.10\nDB,10.0.0.20",
        "22,80,443",
        count=4,
        timeout_ms=1000,
        continuous=True,
    )

    assert worker_counts == [6]
    assert len(results) == 6
    assert {(result.target, result.port) for result in results} == {
        ("10.0.0.10", 22),
        ("10.0.0.10", 80),
        ("10.0.0.10", 443),
        ("10.0.0.20", 22),
        ("10.0.0.20", 80),
        ("10.0.0.20", 443),
    }
