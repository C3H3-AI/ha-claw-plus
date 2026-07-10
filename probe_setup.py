import sys, traceback, asyncio

sys.path.insert(0, "/config/custom_components")

# 触发 HA 完整运行时（config_dir 可见，能真实复现 setup）
from homeassistant import runner
# 不启动完整 HA，只加载 core  module 以拿到 HomeAssistant 类定义
from homeassistant.core import HomeAssistant

import claw_plus
from claw_plus.const import DOMAIN

print("MODULE LOADED")

# 检查 _register_dashboard 是否引用了正确符号
import inspect
src = inspect.getsource(claw_plus._register_dashboard)
print("--- _register_dashboard source head ---")
print("\n".join(src.splitlines()[:40]))

# 检查 async_setup_entry 是否调用了 _register_dashboard
src2 = inspect.getsource(claw_plus.async_setup_entry)
print("--- async_setup_entry calls _register_dashboard? ---")
print("_register_dashboard" in src2)

# 检查 ClawDashboardView 是否定义了 get 方法
print("--- ClawDashboardView.get defined? ---", hasattr(claw_plus.ClawDashboardView, "get"))

# 模拟注册：尝试用假的 hass 对象调用 register_view 看是否抛异常
class FakeHTTP:
    def register_view(self, view):
        print("register_view called with:", view)
        # 验证 view 有 url/name
        print("  view.url =", getattr(view, "url", "MISSING"))
        print("  view.name =", getattr(view, "name", "MISSING"))

class FakeHass:
    def __init__(self):
        self.http = FakeHTTP()
        self.data = {}

try:
    fh = FakeHass()
    # 只测 _register_dashboard 里 register_view 之前的 www 检查 + register_view
    claw_plus.hass.http.register_view(claw_plus.ClawDashboardView)
    print("REGISTER VIEW OK (static check)")
except Exception as e:
    print("REGISTER ERROR:", traceback.format_exc())
