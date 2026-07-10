import os, json

# Replicate get_option_schema EXACTLY as the backend does
from homeassistant.core import HomeAssistant

# We can't easily get hass here; instead probe config_dir candidates
candidates = [
    "/config",
    os.environ.get("HA_CONFIG", ""),
]
print("HASS_CONFIG env:", os.environ.get("HA_CONFIG"))

for cfg in candidates:
    if not cfg:
        continue
    tp = os.path.join(cfg, "custom_components", "claw_assistant", "translations", "zh-Hans.json")
    print(f"\ncandidate config_dir={cfg}")
    print("  trans_path exists:", os.path.isfile(tp))
    if os.path.isfile(tp):
        try:
            trans = json.loads(open(tp, encoding="utf-8").read())
            opt_steps = trans.get("options", {}).get("step", {})
            init = opt_steps.get("init", {})
            mo = init.get("menu_options")
            print("  init menu_options count:", len(mo) if mo else 0)
            print("  menu_tree children would be:", list(mo.keys()) if mo else "NONE")
        except Exception as e:
            print("  READ ERROR:", e)

# Also: what does the LIVE api actually return? hit it locally
import urllib.request
try:
    req = urllib.request.Request("http://localhost:8123/api/claw_plus/dashboard",
        data=json.dumps({"action": "get_option_schema"}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    r = json.loads(urllib.request.urlopen(req, timeout=10).read())
    mt = r.get("menu_tree", {})
    print("\n=== LIVE API menu_tree ===")
    print("  ok:", r.get("ok"))
    print("  menu_tree keys:", list(mt.keys()))
    print("  menu_tree.children:", list(mt.get("children", {}).keys()) if isinstance(mt.get("children"), dict) else mt.get("children"))
    print("  field_meta steps:", list(r.get("field_meta", {}).keys())[:10])
except Exception as e:
    print("LIVE API ERROR:", e)
