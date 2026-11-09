"""Synthetic caches and real scope/helper schemas; never inspect the host LAN."""

import json
import os
import socket
import sqlite3
import struct
import subprocess
from dataclasses import FrozenInstanceError, replace

import pytest

from commonplace.compute.discovery import (
    MAX_LINE_BYTES,
    MAX_SOURCE_BYTES,
    MAX_SOURCE_LINES,
    CacheSnapshot,
    DiscoveryDenied,
    OSCacheSupplier,
    PassiveCollector,
    parse_cache,
)
from commonplace.compute.protocol import (
    MAX_FRAME_BYTES,
    GatewayEnrollment,
    GatewayHelper,
    HostKeyPin,
    Limits,
    PassiveDiscoveryRequest,
    ProtocolDenied,
    ReplayJournal,
    canonical,
    decode_frame,
    encode_frame,
)
from commonplace.compute.scopes import DiscoveryContext, DiscoveryLimits, NetworkScope

MAC = "02:00:00:00:00:01"


def neigh(address="10.77.0.3", interface="eth0", mac=MAC, state="STALE"):
    return f"{address} dev {interface} lladdr {mac} {state}\n".encode()


def arp(address="10.77.0.3", interface="eth0", mac=MAC, flags="0x2"):
    return f"{address} 0x1 {flags} {mac} * {interface}\n".encode()


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Supplier:
    sources = frozenset({"linux_arp", "ip_neigh"})

    def __init__(self, data=None, *, observed_at=1000, before=None):
        self.data = {"ip_neigh": neigh()} if data is None else data
        self.observed_at = observed_at
        self.before = before
        self.calls = []

    def read(self, source, *, max_bytes, max_lines, checkpoint):
        self.calls.append((source, max_bytes, max_lines))
        if self.before:
            self.before(source)
        checkpoint()
        return CacheSnapshot(source, self.data[source], self.observed_at)


@pytest.fixture(autouse=True)
def forbid_network_and_processes(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("discovery attempted network, DNS or process access")

    for name in (
        "socket",
        "socketpair",
        "create_connection",
        "getaddrinfo",
        "gethostbyname",
        "gethostbyname_ex",
        "gethostbyaddr",
        "getnameinfo",
    ):
        monkeypatch.setattr(socket, name, forbidden)
    for name in (
        "Popen",
        "run",
        "call",
        "check_call",
        "check_output",
        "getoutput",
        "getstatusoutput",
    ):
        monkeypatch.setattr(subprocess, name, forbidden)
    for name in ("system", "popen", "posix_spawn", "posix_spawnp", "fork", "execv", "execve"):
        if hasattr(os, name):
            monkeypatch.setattr(os, name, forbidden)


@pytest.fixture
def scope():
    return NetworkScope(
        context=DiscoveryContext("workspace", "project", "gateway", "user", "agent", "task"),
        cidrs=("10.77.0.0/28", "fd42:289::/124"),
        interfaces=("eth0", "eth1"),
        methods=frozenset({"passive"}),
        approved_by="owner",
        expires_at=1100,
        limits=DiscoveryLimits(8, 2, 100, 30, 20),
    )


def collect(scope, supplier=None, *, clock=None, **kwargs):
    clock = clock or Clock()
    collector = PassiveCollector(supplier or Supplier(), wall=clock, monotonic=clock)
    args = dict(
        context=scope.context,
        limits=scope.limits,
        sources=("ip_neigh",),
        ceiling=scope,
        current_scope=lambda: scope,
    )
    return collector.collect(**(args | kwargs))


def test_proc_arp_header_complete_flags_and_ipv4_parser():
    data = b"IP address HW type Flags HW address Mask Device\n" + arp(flags="0x6")
    entries = parse_cache("linux_arp", data)
    assert len(entries) == 1
    assert entries[0].address == "10.77.0.3"
    assert entries[0].interface == "eth0"
    assert entries[0].hardware_address == MAC
    assert entries[0].state == "complete"
    with pytest.raises(FrozenInstanceError):
        entries[0].interface = "eth2"


def test_ipv6_numeric_normalization_and_neigh_states():
    data = neigh("FD42:0289:0:0:0:0:0:2", state="router REACHABLE")
    data += neigh("10.77.0.4", state="PERMANENT")
    data += neigh("fd42:289::3", state="DELAY")
    entries = parse_cache("ip_neigh", data)
    assert [x.address for x in entries] == ["fd42:289::2", "10.77.0.4", "fd42:289::3"]
    assert entries[0].state == "REACHABLE"


@pytest.mark.parametrize(
    "address",
    (
        "pi.local",
        "10.077.0.3",
        "127.0.0.1",
        "169.254.1.1",
        "8.8.8.8",
        "192.0.2.1",
        "224.0.0.1",
        "0.0.0.0",
        "::",
        "::1",
        "fe80::1",
        "ff02::1",
        "::ffff:10.77.0.3",
        "fd42:289::2%eth0",
        "https://10.77.0.3",
        "10.77.0.3;id",
    ),
)
def test_only_numeric_private_addresses_are_parsed(address):
    assert parse_cache("ip_neigh", neigh(address)) == ()
    assert parse_cache("linux_arp", arp(address)) == ()


@pytest.mark.parametrize(
    "data",
    (
        arp(flags="0x0"),
        arp(mac="00:00:00:00:00:00"),
        arp(mac="ff:ff:ff:ff:ff:ff"),
        arp(mac="01:00:00:00:00:01"),
        arp(mac="bad"),
        arp(interface="eth0;id"),
        arp().replace(b"0x1", b"0x2"),
        arp().replace(b" * ", b" 0.0.0.0 "),
    ),
)
def test_incomplete_or_unsupported_arp_records_are_discarded(data):
    assert parse_cache("linux_arp", data) == ()


@pytest.mark.parametrize(
    "data",
    (
        neigh(state="FAILED"),
        neigh(state="INCOMPLETE"),
        neigh(state="STALE REACHABLE"),
        neigh(state="STALE STALE"),
        neigh(state="STALE hostname=pi.local"),
        neigh(state="STALE <script>alert(1)</script>"),
        neigh(state="STALE $(touch /tmp/no)"),
        neigh(interface="eth0\x1b[1m"),
        neigh(mac="02:00:00:00:00:01\x00"),
        b"10.77.0.3 dev eth0 INCOMPLETE\n",
        neigh().replace(b"dev", b"via"),
        neigh().replace(b"STALE", b"ST\xffALE"),
    ),
)
def test_unknown_or_injected_neigh_metadata_is_inert_and_dropped(data):
    assert parse_cache("ip_neigh", data) == ()


def test_crlf_tabs_and_empty_records_are_bounded():
    assert (
        len(parse_cache("ip_neigh", b"\n" + neigh().replace(b" ", b"\t").replace(b"\n", b"\r\n")))
        == 1
    )
    assert parse_cache("ip_neigh", b"") == ()


@pytest.mark.parametrize(
    "data,reason",
    (
        (b"x" * (MAX_SOURCE_BYTES + 1), "source_byte_limit"),
        (b"\n" * (MAX_SOURCE_LINES + 1), "source_line_limit"),
        (b"x" * (MAX_LINE_BYTES + 1), "source_line_limit"),
        ("text", "source_byte_limit"),
    ),
)
def test_parser_input_bounds_include_malformed_data(data, reason):
    with pytest.raises(DiscoveryDenied, match=reason):
        parse_cache("ip_neigh", data)


def test_exact_numeric_scope_and_complete_provenance(scope):
    data = neigh() + neigh("fd42:289::2") + neigh("10.77.1.3") + neigh(interface="eth2")
    data += neigh("10.77.0.0") + neigh("10.77.0.15")
    result = collect(scope, Supplier({"ip_neigh": data}))
    assert result.reason is None
    assert [x.address for x in result.observations] == ["10.77.0.3", "fd42:289::2"]
    observation = result.observations[0]
    assert observation.document() == dict(
        gateway="gateway",
        interface="eth0",
        source="ip_neigh",
        observed_at=1000,
        address="10.77.0.3",
        hardware_address=MAC,
        state="STALE",
    )
    assert result.source_bytes == len(data)
    assert result.source_lines == 6


def test_point_to_point_and_single_host_scope_endpoints_are_usable(scope):
    narrowed = replace(scope, cidrs=("10.77.0.2/31", "10.77.0.4/32"))
    result = collect(narrowed, Supplier({"ip_neigh": neigh("10.77.0.2") + neigh("10.77.0.4")}))
    assert [x.address for x in result.observations] == ["10.77.0.2", "10.77.0.4"]


def test_dedup_preserves_interfaces_sources_and_conflicting_hardware(scope):
    data = neigh() * 2 + neigh(interface="eth1") + neigh(mac="02:00:00:00:00:02")
    supplier = Supplier({"ip_neigh": data, "linux_arp": arp()})
    result = collect(scope, supplier, sources=("ip_neigh", "linux_arp"))
    assert len(result.observations) == 4
    assert {x.source for x in result.observations} == {"ip_neigh", "linux_arp"}
    assert {x.interface for x in result.observations} == {"eth0", "eth1"}
    assert len({x.hardware_address for x in result.observations}) == 2
    assert result.source_lines == 5


@pytest.mark.parametrize("field", ("workspace", "project", "gateway", "user", "agent", "task"))
def test_wrong_actor_or_task_is_denied_before_reading(scope, field):
    supplier = Supplier()
    with pytest.raises(DiscoveryDenied, match="context"):
        collect(scope, supplier, context=replace(scope.context, **{field: "foreign"}))
    assert supplier.calls == []


@pytest.mark.parametrize(
    "changes", ({"revoked": True}, {"expires_at": 1000}, {"methods": frozenset({"active"})})
)
def test_expired_revoked_or_active_only_scope_never_reads(scope, changes):
    supplier = Supplier()
    with pytest.raises(DiscoveryDenied):
        collect(scope, supplier, current_scope=lambda: replace(scope, **changes))
    assert supplier.calls == []


def test_independent_protected_ceiling_intersects_current_grant(scope):
    ceiling = replace(scope, cidrs=("10.77.0.0/30",), interfaces=("eth0",))
    supplier = Supplier(
        {"ip_neigh": neigh("10.77.0.2") + neigh("10.77.0.4") + neigh(interface="eth1")}
    )
    result = collect(scope, supplier, ceiling=ceiling)
    assert [x.address for x in result.observations] == ["10.77.0.2"]
    supplier.calls.clear()
    with pytest.raises(DiscoveryDenied, match="limit"):
        collect(scope, supplier, ceiling=replace(ceiling, limits=replace(scope.limits, hosts=4)))
    assert supplier.calls == []


@pytest.mark.parametrize(
    "field", ("hosts", "concurrency", "rate_per_second", "duration_seconds", "results")
)
def test_every_requested_limit_must_fit_both_independent_scopes(scope, field):
    # Start below every domain maximum, then request one unit over the grant.
    granted = replace(scope, limits=DiscoveryLimits(8, 2, 4, 5, 10))
    requested = replace(granted.limits, **{field: getattr(granted.limits, field) + 1})
    supplier = Supplier()
    with pytest.raises(DiscoveryDenied, match="limit"):
        collect(granted, supplier, limits=requested)
    assert supplier.calls == []
    with pytest.raises(DiscoveryDenied, match="limit"):
        collect(
            scope,
            supplier,
            limits=requested,
            current_scope=lambda: granted,
            ceiling=replace(scope, limits=DiscoveryLimits(16, 4, 100, 30, 20)),
        )
    assert supplier.calls == []


def test_live_revocation_after_source_read_withholds_all_observations(scope):
    current = [scope]
    supplier = Supplier(before=lambda source: current.__setitem__(0, replace(scope, revoked=True)))
    with pytest.raises(DiscoveryDenied, match="revoked"):
        collect(scope, supplier, current_scope=lambda: current[0])
    assert len(supplier.calls) == 1


def test_authority_rechecked_for_each_observation_and_at_delivery(scope):
    calls = []
    supplier = Supplier({"ip_neigh": neigh() + neigh("10.77.0.4")})

    def current():
        calls.append(len(supplier.calls))
        return scope if len(calls) < 6 else replace(scope, revoked=True)

    with pytest.raises(DiscoveryDenied, match="revoked"):
        collect(scope, supplier, current_scope=current)
    assert len(calls) == 6
    assert calls[0] == 0 and calls[-1] == 1


def test_scope_narrowing_at_final_delivery_does_not_leak_prior_observations(scope):
    calls = 0

    def current():
        nonlocal calls
        calls += 1
        return scope if calls < 6 else replace(scope, cidrs=("10.77.0.8/29",))

    with pytest.raises(DiscoveryDenied, match="scope_changed"):
        collect(scope, current_scope=current)


@pytest.mark.parametrize("stamp", (699, 1001, float("nan"), float("inf"), True, -1))
def test_stale_future_and_invalid_snapshot_times_deny(scope, stamp):
    with pytest.raises(DiscoveryDenied):
        collect(scope, Supplier(observed_at=stamp))


@pytest.mark.parametrize(
    "sources", (("mdns",), ("dns",), ("active",), ("ip_neigh;id",), (), ("ip_neigh", "ip_neigh"))
)
def test_unsupported_or_duplicate_sources_deny_without_reading(scope, sources):
    supplier = Supplier()
    with pytest.raises(DiscoveryDenied, match="source"):
        collect(scope, supplier, sources=sources)
    assert supplier.calls == []


def test_supplier_unavailability_and_foreign_snapshot_are_fail_closed(scope):
    supplier = Supplier({})
    with pytest.raises(DiscoveryDenied, match="passive_source_unavailable"):
        collect(scope, supplier)
    supplier.read = lambda *args, **kwargs: CacheSnapshot("linux_arp", arp(), 1000)
    with pytest.raises(DiscoveryDenied, match="invalid_cache_snapshot"):
        collect(scope, supplier)
    with pytest.raises(DiscoveryDenied, match="scope_authority_unavailable"):
        collect(scope, current_scope=lambda: (_ for _ in ()).throw(RuntimeError("untrusted text")))


@pytest.mark.parametrize(
    "field,value,reason",
    (
        ("hosts", 2, "host_limit"),
        ("results", 2, "result_limit"),
        ("rate_per_second", 2, "rate_limit"),
    ),
)
def test_aggregate_host_result_and_rate_bounds(scope, field, value, reason):
    limits = replace(scope.limits, **{field: value})
    supplier = Supplier({"ip_neigh": neigh() + neigh("10.77.0.4"), "linux_arp": arp("10.77.0.5")})
    result = collect(scope, supplier, sources=("ip_neigh", "linux_arp"), limits=limits)
    assert result.reason == reason
    assert len(result.observations) == 2
    assert result.document()["truncated"] is True


def test_same_host_different_sources_consumes_results_but_not_two_host_slots(scope):
    limits = replace(scope.limits, hosts=1, concurrency=1)
    result = collect(
        scope,
        Supplier({"ip_neigh": neigh(), "linux_arp": arp()}),
        sources=("ip_neigh", "linux_arp"),
        limits=limits,
    )
    assert result.reason is None and len(result.observations) == 2


def test_rate_budget_is_sliding_and_uses_monotonic_not_wall(scope):
    clock = Clock()
    supplier = Supplier(
        {"ip_neigh": neigh(), "linux_arp": arp("10.77.0.4")},
        before=lambda source: setattr(
            clock, "now", clock.now + (1 if source == "linux_arp" else 0)
        ),
    )
    result = collect(
        scope,
        supplier,
        clock=clock,
        sources=("ip_neigh", "linux_arp"),
        limits=replace(scope.limits, rate_per_second=1),
    )
    assert result.reason is None and len(result.observations) == 2


@pytest.mark.parametrize("kind", ("bytes", "lines"))
def test_source_consumption_is_aggregate_and_counts_invalid_records(scope, kind):
    data = (b"x" * 1023 + b"\n") * 40 if kind == "bytes" else b"\n" * 700
    supplier = Supplier({"ip_neigh": data, "linux_arp": data})
    with pytest.raises(DiscoveryDenied, match="source_.*limit"):
        collect(scope, supplier, sources=("ip_neigh", "linux_arp"))
    assert supplier.calls[1][1] == MAX_SOURCE_BYTES - len(data)
    assert supplier.calls[1][2] < MAX_SOURCE_LINES


def test_cancelled_before_source_and_during_source_does_not_continue(scope):
    supplier = Supplier()
    result = collect(scope, supplier, cancelled=lambda: True)
    assert result.reason == "cancelled" and result.observations == ()
    assert supplier.calls == []
    cancel = [False]
    supplier.before = lambda source: cancel.__setitem__(0, True)
    result = collect(scope, supplier, cancelled=lambda: cancel[0])
    assert result.reason == "cancelled" and result.observations == ()
    assert len(supplier.calls) == 1


def test_cancel_between_sources_preserves_bounded_partial_provenance(scope):
    cancel = [False]
    supplier = Supplier(
        {"ip_neigh": neigh(), "linux_arp": arp()},
        before=lambda source: cancel.__setitem__(0, source == "linux_arp"),
    )
    result = collect(
        scope, supplier, sources=("ip_neigh", "linux_arp"), cancelled=lambda: cancel[0]
    )
    assert result.reason == "cancelled"
    assert [x.source for x in result.observations] == ["ip_neigh"]


def test_duration_checked_after_supplier_and_clock_regression_denies(scope):
    clock = Clock()
    supplier = Supplier(before=lambda source: setattr(clock, "now", 1030))
    result = collect(scope, supplier, clock=clock)
    assert result.reason == "duration_limit" and result.observations == ()
    clock.now = 1000
    supplier.before = lambda source: setattr(clock, "now", 999)
    with pytest.raises(DiscoveryDenied, match="clock_regressed"):
        collect(scope, supplier, clock=clock)


def test_expiry_during_authority_lookup_is_rechecked(scope):
    clock = Clock()

    def current():
        clock.now = 1100
        return scope

    with pytest.raises(DiscoveryDenied, match="expired"):
        collect(scope, clock=clock, current_scope=current)


def test_wall_clock_change_does_not_reset_monotonic_duration(scope):
    wall, monotonic = Clock(), Clock()
    supplier = Supplier(before=lambda source: setattr(monotonic, "now", 1030))
    collector = PassiveCollector(supplier, wall=wall, monotonic=monotonic)
    result = collector.collect(
        context=scope.context,
        limits=scope.limits,
        sources=("ip_neigh",),
        ceiling=scope,
        current_scope=lambda: scope,
    )
    assert result.reason == "duration_limit" and result.observations == ()
    assert wall.now == 1000


@pytest.mark.parametrize("stamp", (float("nan"), float("inf"), True, -1, 10**500))
def test_invalid_runtime_clocks_cannot_authorize_observations(scope, stamp):
    supplier = Supplier()
    with pytest.raises(DiscoveryDenied, match="invalid_clock"):
        collect(scope, supplier, clock=lambda: stamp)
    assert supplier.calls == []


def test_wall_clock_regression_fails_closed_even_with_valid_monotonic(scope):
    wall, monotonic = Clock(), Clock()
    supplier = Supplier(before=lambda source: setattr(wall, "now", 999))
    with pytest.raises(DiscoveryDenied, match="clock_regressed"):
        PassiveCollector(supplier, wall=wall, monotonic=monotonic).collect(
            context=scope.context,
            limits=scope.limits,
            sources=("ip_neigh",),
            ceiling=scope,
            current_scope=lambda: scope,
        )


def test_collector_admits_no_parallel_collection_or_reentrant_source(scope):
    clock = Clock()
    supplier = Supplier()
    collector = PassiveCollector(supplier, wall=clock, monotonic=clock)
    args = dict(
        context=scope.context,
        limits=scope.limits,
        sources=("ip_neigh",),
        ceiling=scope,
        current_scope=lambda: scope,
    )
    nested = []

    def reenter(source):
        with pytest.raises(DiscoveryDenied, match="capacity"):
            collector.collect(**args)
        nested.append(source)

    supplier.before = reenter
    assert len(collector.collect(**args).observations) == 1
    assert nested == ["ip_neigh"]


def test_encoded_output_budget_includes_summary_and_provenance(scope):
    data = b"".join(neigh(f"10.77.0.{i}") for i in range(1, 9))
    result = collect(scope, Supplier({"ip_neigh": data}), output_bytes=600, envelope_bytes=100)
    assert result.reason == "output_limit"
    assert 0 < len(result.observations) < 8
    assert len(canonical(result.document())) + 100 <= 600
    supplier = Supplier()
    with pytest.raises(DiscoveryDenied, match="output_limit"):
        collect(scope, supplier, output_bytes=10)
    assert supplier.calls == []


def test_os_supplier_uses_only_fixed_source_path_and_synthetic_file(scope, tmp_path, monkeypatch):
    path = tmp_path / "synthetic-arp"
    path.write_bytes(arp())
    real_open = os.open
    opened = []

    def open_cache(name, flags):
        assert name == "/proc/net/arp"
        assert flags & os.O_NOFOLLOW and flags & os.O_NONBLOCK
        opened.append(name)
        return real_open(path, flags)

    reader = OSCacheSupplier(wall=Clock())
    with monkeypatch.context() as patch:
        patch.setattr(os, "open", open_cache)
        result = collect(scope, reader, sources=("linux_arp",))
    assert len(result.observations) == 1 and opened == ["/proc/net/arp"]
    with pytest.raises(DiscoveryDenied, match="unsupported"):
        reader.read("/etc/passwd", max_bytes=100, max_lines=10, checkpoint=lambda: None)
    assert len(opened) == 1


@pytest.mark.parametrize(
    "data,max_bytes,max_lines,reason",
    (
        (arp(), 10, 10, "source_byte_limit"),
        (b"\n" * 4, 100, 3, "source_line_limit"),
        (b"x" * 1025, 2000, 10, "source_line_limit"),
    ),
)
def test_os_reader_enforces_bounds_and_closes_synthetic_descriptors(
    tmp_path, monkeypatch, data, max_bytes, max_lines, reason
):
    path = tmp_path / "cache"
    path.write_bytes(data)
    real_open, real_close = os.open, os.close
    closed = []

    def close(fd):
        closed.append(fd)
        real_close(fd)

    with monkeypatch.context() as patch:
        patch.setattr(os, "open", lambda name, flags: real_open(path, flags))
        patch.setattr(os, "close", close)
        with pytest.raises(DiscoveryDenied, match=reason):
            OSCacheSupplier(wall=Clock()).read(
                "linux_arp", max_bytes=max_bytes, max_lines=max_lines, checkpoint=lambda: None
            )
    assert len(closed) == 1


class Wire:
    def __init__(self, incoming):
        self.incoming, self.sent, self.closed = bytearray(incoming), bytearray(), False

    def recv(self, count):
        data = bytes(self.incoming[:count])
        del self.incoming[:count]
        return data

    def settimeout(self, timeout):
        assert timeout > 0

    def sendall(self, data):
        self.sent.extend(data)

    def close(self):
        self.closed = True


@pytest.fixture
def helper_setup(scope):
    db = sqlite3.connect(":memory:")
    clock = Clock()
    gateway = GatewayEnrollment(
        "workspace",
        "gateway",
        "192.0.2.10",
        22,
        "gateway-user",
        HostKeyPin("ssh-ed25519", b"g" * 32),
        1,
        2,
        "gateway-key",
    )
    request = PassiveDiscoveryRequest(
        "discovery-1", "scope-1", 1, 8, scope.context, ("ip_neigh",), scope.limits
    )
    supplier = Supplier()
    authority = [scope]
    checks = []

    def authorize(request, phase):
        checks.append((request, phase))
        return (
            request.scope == "scope-1"
            and request.context == scope.context
            and request.authority_epoch == 8
            and phase == "collect_passive"
        )

    def forbidden_route(*args):
        pytest.fail("passive collection attempted to open a route")

    def make(**kwargs):
        args = dict(
            binding=("workspace", "gateway"),
            enabled=True,
            wall=clock,
            monotonic=clock,
            passive_collector=PassiveCollector(supplier, wall=clock, monotonic=clock),
            passive_ceiling=scope,
            passive_scope=lambda request: authority[0],
        )
        return GatewayHelper(
            gateway, [], ReplayJournal(db), authorize, forbidden_route, **(args | kwargs)
        )

    yield make, request, supplier, authority, checks, db
    db.close()


def test_typed_request_roundtrip_exact_fields_and_no_caller_grant(helper_setup):
    _, request, _, _, _, _ = helper_setup
    assert (
        PassiveDiscoveryRequest.from_message(decode_frame(encode_frame(request.message())))
        == request
    )
    with pytest.raises(FrozenInstanceError):
        request.scope = "other"
    for field, value in (
        ("cidrs", ["10.0.0.0/8"]),
        ("approved_by", "owner"),
        ("path", "/proc/net/arp"),
        ("command", "ip neigh"),
        ("revoked", False),
        ("grant", {}),
    ):
        with pytest.raises(ProtocolDenied, match="invalid_fields"):
            PassiveDiscoveryRequest.from_message(request.message() | {field: value})
    for field in ("context", "limits"):
        message = request.message()
        message[field]["extra"] = 1
        with pytest.raises(ProtocolDenied, match="invalid_fields"):
            PassiveDiscoveryRequest.from_message(message)


@pytest.mark.parametrize(
    "changes",
    (
        {"method": "active"},
        {"gateway_epoch": True},
        {"authority_epoch": 0},
        {"sources": ("mdns",)},
        {"output_bytes": MAX_FRAME_BYTES + 1},
        {"output_bytes": True},
        {"scope": "scope;id"},
    ),
)
def test_typed_request_denies_active_or_malformed_contract(helper_setup, changes):
    _, request, _, _, _, _ = helper_setup
    with pytest.raises(ProtocolDenied):
        replace(request, **changes)


def test_helper_actual_framed_collection_keeps_observations_unmanaged(helper_setup):
    make, request, supplier, _, checks, db = helper_setup
    wire = Wire(encode_frame(request.message()))
    make().serve(wire)
    response = decode_frame(bytes(wire.sent))
    assert response["kind"] == "passive_observations"
    assert response["discovery"] == request.discovery
    assert response["observations"][0]["source"] == "ip_neigh"
    assert len(supplier.calls) == 1 and len(checks) >= 4
    assert all(phase == "collect_passive" for _, phase in checks)
    assert wire.closed and len(wire.sent) - 4 <= request.output_bytes
    assert db.execute("SELECT count(*) FROM compute_transport_journal").fetchone()[0] == 0
    assert all(x not in json.dumps(response) for x in ("enrolled", "credential", "instruction"))


def test_helper_default_unavailable_and_no_implicit_os_read(helper_setup):
    make, request, supplier, _, _, _ = helper_setup
    helper = make(passive_collector=None, passive_ceiling=None, passive_scope=None)
    for message in (
        request.message(),
        {"v": 1, "kind": "collect_passive", "workspace": "workspace", "gateway": "gateway"},
    ):
        wire = Wire(encode_frame(message))
        with pytest.raises(ProtocolDenied, match="passive_source_unavailable"):
            helper.serve(wire)
        assert not wire.sent and wire.closed
    wire = Wire(
        encode_frame({"v": 1, "kind": "describe", "workspace": "workspace", "gateway": "gateway"})
    )
    helper.serve(wire)
    assert decode_frame(bytes(wire.sent))["passive_sources"] == []
    assert not supplier.calls


@pytest.mark.parametrize("field", ("workspace", "project", "gateway", "user", "agent", "task"))
def test_helper_authentication_and_scope_reject_every_foreign_context(helper_setup, field):
    make, request, supplier, _, _, _ = helper_setup
    forged = replace(request, context=replace(request.context, **{field: "foreign"}))
    with pytest.raises(ProtocolDenied):
        make().collect_passive(forged)
    assert supplier.calls == []


def test_helper_stale_epochs_revocation_and_ceiling_deny(helper_setup, scope):
    make, request, supplier, authority, _, _ = helper_setup
    for changes in ({"gateway_epoch": 2}, {"authority_epoch": 9}, {"scope": "foreign"}):
        with pytest.raises(ProtocolDenied):
            make().collect_passive(replace(request, **changes))
    authority[0] = replace(scope, revoked=True)
    with pytest.raises(ProtocolDenied):
        make().collect_passive(request)
    authority[0] = scope
    with pytest.raises(ProtocolDenied):
        make(
            passive_ceiling=replace(scope, limits=replace(scope.limits, results=1))
        ).collect_passive(request)
    assert supplier.calls == []


def test_helper_output_ceiling_and_trusted_cancellation(helper_setup):
    make, request, supplier, _, _, _ = helper_setup
    supplier.data["ip_neigh"] = b"".join(neigh(f"10.77.0.{i}") for i in range(1, 9))
    response = make(limits=Limits(output_bytes=650)).collect_passive(
        replace(request, output_bytes=700)
    )
    assert response["reason"] == "output_limit"
    assert len(canonical(response)) <= 650
    assert len(encode_frame(response)) <= MAX_FRAME_BYTES + 4
    response = make().collect_passive(request, cancelled=lambda: True)
    assert response["reason"] == "cancelled" and response["observations"] == []


def test_helper_requires_all_trusted_configuration_parts(helper_setup, scope):
    make, _, _, _, _, _ = helper_setup
    with pytest.raises(ProtocolDenied, match="invalid_passive_configuration"):
        make(passive_scope=None)
    with pytest.raises(ProtocolDenied, match="invalid_passive_configuration"):
        make(passive_ceiling=replace(scope, context=replace(scope.context, gateway="foreign")))


def test_discovery_frame_ceiling_duplicate_nested_keys_and_unknown_fields(helper_setup):
    make, request, supplier, _, _, _ = helper_setup
    encoded = canonical(request.message()).replace(b'"hosts":8', b'"hosts":8,"hosts":8')
    with pytest.raises(ProtocolDenied):
        make().serve(Wire(struct.pack("!I", len(encoded)) + encoded))
    with pytest.raises(ProtocolDenied, match="invalid_frame_size"):
        make().serve(Wire(struct.pack("!I", MAX_FRAME_BYTES + 1)))
    message = request.message()
    message["context"]["approved_by"] = "owner"
    with pytest.raises(ProtocolDenied, match="invalid_fields"):
        make().serve(Wire(encode_frame(message)))
    assert supplier.calls == []
