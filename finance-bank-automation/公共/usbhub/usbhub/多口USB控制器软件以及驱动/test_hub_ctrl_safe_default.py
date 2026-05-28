# -*- coding: utf-8 -*-
"""hub_ctrl.py 安全默认值回归测试（不触碰真实串口/硬件）。

通过 monkeypatch 把 serial.Serial 换成一个纯内存假串口，确保整个 _send/_open
逻辑照常执行，但绝不会打开 COM 口或控制真实 USB Hub。

验证点：
  1. 新建 USBHub 默认全部断电 (mask 全 0xFF)，而不是旧的 all-on(0x00)。
  2. 一次性 `on 12` 只打开 12 口，其它口保持断电。
  3. `off 29` 不会顺手打开任何其它口。
  4. 多口 `on [1, 29]` 只打开这两口。

直接 `python test_hub_ctrl_safe_default.py` 运行；不依赖 pytest。
"""
import importlib.util
import sys
from pathlib import Path


def _load_hub_module():
    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location("hub_ctrl", here / "hub_ctrl.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeSerial:
    """纯内存假串口：只记录写入，read 返回固定 2 字节，绝不碰硬件。"""

    instances = []

    def __init__(self):
        self.port = None
        self.baudrate = None
        self.timeout = None
        self.dtr = None
        self.rts = None
        self.writes = []
        self.opened = False
        FakeSerial.instances.append(self)

    def open(self):
        # 故意不连接任何真实端口
        self.opened = True

    def write(self, data):
        self.writes.append(bytes(data))

    def read(self, n):
        return b"\x00" * n

    def close(self):
        self.opened = False


def _make_hub(hub_mod):
    FakeSerial.instances.clear()
    hub_mod.serial.Serial = FakeSerial  # monkeypatch：屏蔽真实串口
    return hub_mod.USBHub(port="FAKE")


def _mask_after(hub_mod, action, ports):
    """执行一次操作并返回操作后的 mask 副本。"""
    hub = _make_hub(hub_mod)
    getattr(hub, action)(*ports)
    return dict(hub.mask)


def _expect_only(hub_mod, mask, open_ports):
    """断言：只有 open_ports 对应的 bit 被清零(通电)，其它位全部为 1(断电)。"""
    expected = {bank: 0xFF for bank in hub_mod.BANKS}
    for port in open_ports:
        bank, bit = hub_mod.PORT_BIT[port]
        expected[bank] &= ~(1 << bit) & 0xFF
    assert mask == expected, f"open_ports={open_ports} 期望 {expected}，实际 {mask}"


def main():
    hub_mod = _load_hub_module()

    # 1. 默认全部断电
    hub = _make_hub(hub_mod)
    assert hub.mask == {bank: 0xFF for bank in hub_mod.BANKS}, (
        f"默认 mask 应全 0xFF(断电)，实际 {hub.mask}"
    )
    # 没有任何真实串口被打开
    assert not FakeSerial.instances or all(
        not s.opened for s in FakeSerial.instances
    ), "构造 USBHub 不应打开串口"

    # 2. on 12 只开 12
    mask = _mask_after(hub_mod, "on", [12])
    _expect_only(hub_mod, mask, [12])

    # 3. off 29 不开任何口
    mask = _mask_after(hub_mod, "off", [29])
    _expect_only(hub_mod, mask, [])

    # 4. 多口 set_ports([1, 29], True) 只开这两口
    hub = _make_hub(hub_mod)
    hub.set_ports([1, 29], True)
    _expect_only(hub_mod, dict(hub.mask), [1, 29])

    # 5. only_ports([12]) 同样独占，只开 12
    hub = _make_hub(hub_mod)
    hub.only_ports([12], settle_seconds=0)
    _expect_only(hub_mod, dict(hub.mask), [12])

    # 6. all_off 全部断电
    hub = _make_hub(hub_mod)
    hub.all_off()
    assert hub.mask == {bank: 0xFF for bank in hub_mod.BANKS}

    # 整轮跑下来确认所有假串口最终都已关闭
    assert all(not s.opened for s in FakeSerial.instances), "_send 结束后串口应关闭"

    print("所有安全默认值测试通过 (未触碰真实串口/硬件)")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print("测试失败:", exc)
        sys.exit(1)
