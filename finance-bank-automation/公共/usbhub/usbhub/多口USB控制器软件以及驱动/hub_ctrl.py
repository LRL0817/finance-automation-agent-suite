# -*- coding: utf-8 -*-
"""绿精灵 USB hub 控制脚本 (V5.10 协议)
依赖: pip install pyserial
"""
import argparse
import json
import os
import serial
import time

# V5.10 绿精灵协议按 bank/bit 控制端口。当前机器已换成 30 口 Hub：
# 2026-05-25 实测 COM3，29 口农行盾 = bank2 bit5，30 口外置 OK 点击器 = bank2 bit6。
# 当前 30 口 Hub 的逻辑位与物理口并非连续顺序；以下表按本机实测映射维护。
# 若后续厂商硬件修订导致映射不同，可用 USB_HUB_PORT_MAP 覆盖：
#   JSON: {"1":[0,0],"29":[2,5]}
#   text: 1=0:0,29=2:5,30=2:6
DEFAULT_BANK_ORDER = (0, 1, 2, 4)
DEFAULT_PORT_BIT = {
    **{port: (0, port - 1) for port in range(1, 9)},
    9: (1, 0),
    10: (1, 1),
    11: (1, 6),
    12: (1, 7),
    13: (4, 6),
    14: (4, 5),
    15: (2, 7),
    16: (1, 5),
    17: (1, 4),
    18: (1, 3),
    19: (1, 2),
    20: (4, 7),
    21: (4, 2),
    22: (4, 3),
    23: (4, 4),
    24: (2, 0),
    **{port: (2, port - 24) for port in range(25, 31)},
}
DEFAULT_COM = os.getenv("USB_HUB_COM", "COM3")


def _parse_port_map(raw: str) -> dict[int, tuple[int, int]]:
    text = (raw or "").strip()
    if not text:
        return _validate_port_map(dict(DEFAULT_PORT_BIT))

    parsed: dict[int, tuple[int, int]] = {}
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("USB_HUB_PORT_MAP JSON 必须是对象")
        for port_text, value in data.items():
            if not isinstance(value, (list, tuple)) or len(value) != 2:
                raise ValueError(f"端口 {port_text!r} 的映射必须是 [bank, bit]")
            parsed[int(port_text)] = (int(value[0]), int(value[1]))
    except json.JSONDecodeError:
        for item in text.split(","):
            item = item.strip()
            if not item:
                continue
            left, right = item.split("=", 1)
            bank, bit = right.split(":", 1)
            parsed[int(left.strip())] = (int(bank.strip()), int(bit.strip()))

    return _validate_port_map(parsed)


def _validate_port_map(parsed: dict[int, tuple[int, int]]) -> dict[int, tuple[int, int]]:
    if not parsed:
        raise ValueError("USB_HUB_PORT_MAP 未解析出任何端口映射")
    for port, (bank, bit) in parsed.items():
        if port < 1:
            raise ValueError(f"端口号必须 >=1: {port}")
        if bank not in DEFAULT_BANK_ORDER:
            raise ValueError(f"bank 只允许 {DEFAULT_BANK_ORDER}: port={port}, bank={bank}")
        if not 0 <= bit <= 7:
            raise ValueError(f"bit 必须在 0-7: port={port}, bit={bit}")
    reverse = {}
    for port, pair in parsed.items():
        if pair in reverse:
            raise ValueError(
                f"USB Hub 端口映射重复: port {reverse[pair]} 与 port {port} 均为 bank{pair[0]} bit{pair[1]}"
            )
        reverse[pair] = port
    return parsed


PORT_BIT = _parse_port_map(os.getenv("USB_HUB_PORT_MAP", ""))
BANKS = tuple(bank for bank in DEFAULT_BANK_ORDER if bank in {bank for bank, _bit in PORT_BIT.values()})

def crc8(data: bytes, poly=0x07) -> int:
    crc = 0
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = ((crc << 1) ^ poly) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc

def build_frame(bank: int, mask: int) -> bytes:
    body = bytes([0x7E, bank, mask])
    return body + bytes([crc8(body)])


class USBHub:
    def __init__(self, port=DEFAULT_COM, baud=4800, timeout=1.0):
        self.port_name = port
        self.baud = baud
        self.timeout = timeout
        # 协议反逻辑: bit=0 通电, bit=1 断电。
        # 安全默认: 全部置 1 (断电)。否则一次性 CLI `on 12` 会把未指定的口也一起通电，
        # `off 29` 也会意外打开其它口——这违反 USBHub 收尾即断电的安全契约。
        self.mask = {bank: 0xFF for bank in BANKS}

    def close(self):
        pass

    def _open(self):
        ser = serial.Serial()
        ser.port = self.port_name
        ser.baudrate = self.baud
        ser.timeout = self.timeout
        ser.dtr = False
        ser.rts = False
        ser.open()
        return ser

    def _send(self, banks=(0, 1)):
        # 握手帧会把 bank0/1/2/4 全部置为 0xFF, 所以必须在同一会话内
        # 把所有需要控制的 bank 都发一遍, 否则上一次的状态会被握手覆盖。
        if isinstance(banks, int):
            banks = (banks,)
        ser = self._open()
        try:
            for q in (b"\x7e\x00\xff\xb8", b"\x7e\x01\xff\xad",
                      b"\x7e\x02\xff\x92", b"\x7e\x04\xff\xec"):
                ser.write(q); ser.read(2)
            resp = {}
            for b in banks:
                ser.write(build_frame(b, self.mask[b]))
                resp[b] = ser.read(2)
        finally:
            ser.close()
        time.sleep(0.05)
        return resp

    def _validate_ports(self, ports) -> list[int]:
        selected: list[int] = []
        for port in ports:
            if port not in PORT_BIT:
                raise ValueError(f"端口号未配置映射，当前为: {port}；已配置: {sorted(PORT_BIT)}")
            if port not in selected:
                selected.append(port)
        return selected

    def set_port(self, port: int, on: bool):
        return self.set_ports([port], on)

    def set_ports(self, ports, on: bool):
        ports = self._validate_ports(ports)
        if not ports:
            raise ValueError("未指定端口")
        for port in ports:
            bank, bit = PORT_BIT[port]
            # 反逻辑: on 清位, off 置位
            if on:
                self.mask[bank] &= ~(1 << bit) & 0xFF
            else:
                self.mask[bank] |= (1 << bit)
        return self._send(BANKS)

    def on(self, port):
        return self.set_port(port, True)

    def off(self, port):
        return self.set_port(port, False)

    def all_on(self):
        self.mask = {bank: 0x00 for bank in BANKS}
        return self._send(BANKS)

    def all_off(self):
        # 全部 bank/bit 都置 1 (断电)，避免未列入映射的位残留通电。
        self.mask = {bank: 0xFF for bank in BANKS}
        return self._send(BANKS)

    def only(self, port: int, settle_seconds: float = 1.0):
        return self.only_ports([port], settle_seconds=settle_seconds)

    def only_ports(self, ports, settle_seconds: float = 1.0):
        """独占打开指定端口：先关闭所有已配置端口，再只打开指定端口。"""
        ports = self._validate_ports(ports)
        if not ports:
            raise ValueError("未指定端口")
        self.mask = {bank: 0xFF for bank in BANKS}
        self._send(BANKS)
        time.sleep(settle_seconds)
        for port in ports:
            bank, bit = PORT_BIT[port]
            self.mask[bank] &= ~(1 << bit) & 0xFF
        return self._send(BANKS)

    def device_id(self) -> bytes:
        ser = self._open()
        try:
            body = bytes([0x7E, 0x85])
            ser.write(body + bytes([crc8(body)]))
            return ser.read(9)
        finally:
            ser.close()

    # Backward-compatible aliases for older import call sites.
    def set_port_legacy(self, port: int, on: bool):
        if port not in PORT_BIT:
            raise ValueError(f"端口号未配置映射，当前为: {port}；已配置: {sorted(PORT_BIT)}")
        return self.set_port(port, on)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="绿精灵 USB hub 控制脚本")
    parser.add_argument("action", choices=["id", "list", "on", "off", "only", "all-on", "all-off"])
    parser.add_argument("ports", nargs="*", type=int, help="端口号；on/off/only 可指定一个或多个端口。当前已配置 1-30")
    parser.add_argument("--com", default=DEFAULT_COM, help=f"串口号，默认 {DEFAULT_COM}，也可用 USB_HUB_COM 环境变量")
    parser.add_argument("--settle", type=float, default=1.0, help="only 模式下全关后等待秒数，默认 1.0")
    args = parser.parse_args()

    hub = USBHub(args.com)
    print("Device ID:", hub.device_id().hex())

    if args.action in {"on", "off", "only"} and not args.ports:
        raise SystemExit(f"{args.action} 需要指定端口号；当前已配置 {sorted(PORT_BIT)}")

    if args.action == "id":
        pass
    elif args.action == "list":
        for port, (bank, bit) in sorted(PORT_BIT.items()):
            print(f"{port}: bank{bank} bit{bit}")
    elif args.action == "on":
        resp = hub.set_ports(args.ports, True)
        print(f"ports {args.ports} on response:", {k: v.hex() for k, v in resp.items()})
    elif args.action == "off":
        resp = hub.set_ports(args.ports, False)
        print(f"ports {args.ports} off response:", {k: v.hex() for k, v in resp.items()})
    elif args.action == "only":
        resp = hub.only_ports(args.ports, settle_seconds=args.settle)
        print(f"only ports {args.ports} on response:", {k: v.hex() for k, v in resp.items()})
    elif args.action == "all-on":
        resp = hub.all_on()
        print("all ports on response:", {k: v.hex() for k, v in resp.items()})
    elif args.action == "all-off":
        resp = hub.all_off()
        print("all ports off response:", {k: v.hex() for k, v in resp.items()})

    hub.close()
