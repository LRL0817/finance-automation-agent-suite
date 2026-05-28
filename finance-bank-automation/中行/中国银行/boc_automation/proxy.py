import os
import ipaddress
import socket
import random
import struct

from .config import BOC_DIRECT_DNS_SERVER, PROXY_ENV_KEYS

_BOC_DIRECT_HOSTS = ("netc1.igtb.boc.cn", "netc2.igtb.boc.cn")
_FAKE_IP_NETS = (
    ipaddress.ip_network("198.18.0.0/15"),
)
_DIRECT_TCP_TIMEOUT_S = 5.0
_DIRECT_DNS_TIMEOUT_S = 3.0


def _is_fake_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in network for network in _FAKE_IP_NETS)


def _disable_proxy_for_playwright():
    old_env = {key: os.environ.get(key) for key in PROXY_ENV_KEYS}
    for key in PROXY_ENV_KEYS:
        os.environ.pop(key, None)
    return old_env


def _encode_dns_name(host: str) -> bytes:
    parts = host.rstrip(".").split(".")
    encoded = bytearray()
    for part in parts:
        raw = part.encode("ascii")
        if not raw or len(raw) > 63:
            raise ValueError(f"invalid DNS label in host: {host}")
        encoded.append(len(raw))
        encoded.extend(raw)
    encoded.append(0)
    return bytes(encoded)


def _skip_dns_name(packet: bytes, offset: int) -> int:
    while True:
        if offset >= len(packet):
            raise ValueError("truncated DNS name")
        length = packet[offset]
        if length & 0xC0 == 0xC0:
            return offset + 2
        offset += 1
        if length == 0:
            return offset
        offset += length


def _resolve_a_records_via_dns_server(host: str, dns_server: str) -> list[str]:
    query_id = random.randint(0, 0xFFFF)
    question = _encode_dns_name(host) + struct.pack("!HH", 1, 1)  # A, IN
    packet = struct.pack("!HHHHHH", query_id, 0x0100, 1, 0, 0, 0) + question

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(_DIRECT_DNS_TIMEOUT_S)
        sock.sendto(packet, (dns_server, 53))
        response, _addr = sock.recvfrom(4096)

    if len(response) < 12:
        raise ValueError("short DNS response")
    rid, flags, qdcount, ancount, _nscount, _arcount = struct.unpack("!HHHHHH", response[:12])
    if rid != query_id:
        raise ValueError("DNS response id mismatch")
    rcode = flags & 0x000F
    if rcode != 0:
        raise ValueError(f"DNS rcode={rcode}")

    offset = 12
    for _ in range(qdcount):
        offset = _skip_dns_name(response, offset)
        offset += 4

    ips: list[str] = []
    for _ in range(ancount):
        offset = _skip_dns_name(response, offset)
        if offset + 10 > len(response):
            raise ValueError("truncated DNS answer")
        rtype, rclass, _ttl, rdlength = struct.unpack("!HHIH", response[offset : offset + 10])
        offset += 10
        rdata = response[offset : offset + rdlength]
        offset += rdlength
        if rtype == 1 and rclass == 1 and rdlength == 4:
            ips.append(socket.inet_ntoa(rdata))
    return sorted(set(ips))


def _direct_network_preflight() -> dict[str, list[str]]:
    """Fail fast if bank domains are being resolved through proxy/TUN fake-ip DNS."""
    fake_dns = []
    tcp_failures = []
    resolved: dict[str, list[str]] = {}
    for host in _BOC_DIRECT_HOSTS:
        try:
            infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except Exception as exc:
            print(f"[直连预检] {host} DNS 解析失败：{type(exc).__name__}: {exc}", flush=True)
            continue
        ips = sorted({info[4][0] for info in infos})
        print(f"[直连预检] {host} -> {', '.join(ips) if ips else '<empty>'}", flush=True)
        fake_ips = [ip for ip in ips if _is_fake_ip(ip)]
        if fake_ips:
            if not BOC_DIRECT_DNS_SERVER:
                fake_dns.append(f"{host}={','.join(fake_ips)}")
                continue
            try:
                direct_ips = _resolve_a_records_via_dns_server(host, BOC_DIRECT_DNS_SERVER)
            except Exception as exc:
                fake_dns.append(
                    f"{host}={','.join(fake_ips)}; "
                    f"BOC_DIRECT_DNS_SERVER={BOC_DIRECT_DNS_SERVER} 解析失败: {type(exc).__name__}: {exc}"
                )
                continue
            print(
                f"[直连预检] {host} 经 BOC_DIRECT_DNS_SERVER={BOC_DIRECT_DNS_SERVER} -> "
                f"{', '.join(direct_ips) if direct_ips else '<empty>'}",
                flush=True,
            )
            direct_fake_ips = [ip for ip in direct_ips if _is_fake_ip(ip)]
            if not direct_ips or direct_fake_ips:
                fake_dns.append(
                    f"{host}=系统DNS:{','.join(fake_ips)}; "
                    f"BOC_DIRECT_DNS_SERVER:{','.join(direct_ips) if direct_ips else '<empty>'}"
                )
                continue
            ips = direct_ips

        resolved[host] = ips

        connected = False
        errors = []
        for ip in ips:
            try:
                family = socket.AF_INET6 if ":" in ip else socket.AF_INET
                with socket.socket(family, socket.SOCK_STREAM) as sock:
                    sock.settimeout(_DIRECT_TCP_TIMEOUT_S)
                    sock.connect((ip, 443))
                connected = True
                print(f"[直连预检] {host}:443 TCP 直连可用（{ip}）", flush=True)
                break
            except OSError as exc:
                errors.append(f"{ip}:{type(exc).__name__}")
        if not connected:
            tcp_failures.append(f"{host}({', '.join(errors[:4])})")

    if fake_dns:
        hosts = ", ".join(_BOC_DIRECT_HOSTS)
        details = "; ".join(fake_dns)
        raise SystemExit(
            "[终止] 银行域名解析到了代理/TUN fake-ip，不能直连："
            f"{details}。本脚本永远不走代理；请关闭 Meta Tunnel/Clash/Mihomo TUN，"
            f"或把 {hosts} 设置为 DIRECT 并使用真实 DNS 后重试。"
        )

    if tcp_failures:
        details = "; ".join(tcp_failures)
        raise SystemExit(
            "[终止] 银行直连 TCP 443 不通，浏览器无法打开企业网银："
            f"{details}。本脚本永远不走代理；请先恢复当前网络的直连访问，"
            "确认 `curl --noproxy \"*\" -I https://netc1.igtb.boc.cn/` 可连通后再运行。"
        )
    return resolved


def _build_host_resolver_rules(resolved_hosts: dict[str, list[str]]) -> str:
    rules = []
    for host in _BOC_DIRECT_HOSTS:
        ips = resolved_hosts.get(host) or []
        if not ips:
            continue
        rules.append(f"MAP {host} {ips[0]}")
    return ", ".join(rules)


def _restore_proxy_env(old_env) -> None:
    for key, value in old_env.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
