import os, json
base = "/config/custom_components/claw_assistant"
tp = os.path.join(base, "translations", "zh-Hans.json")
print("trans_path exists:", os.path.isfile(tp))
if os.path.isfile(tp):
    d = json.load(open(tp, encoding="utf-8"))
    steps = d.get("options", {}).get("step", {})
    init = steps.get("init", {})
    print("init menu_options:", init.get("menu_options"))
    print("init keys:", list(init.keys()))
    print("all step names:", list(steps.keys()))
    # also check which steps have menu_options
    for k, v in steps.items():
        mo = v.get("menu_options")
        if mo:
            print(f"  step {k} HAS menu_options ({len(mo)} items)")
else:
    print("FILE MISSING - listing translations dir:")
    tdir = os.path.join(base, "translations")
    print("  dir exists:", os.path.isdir(tdir))
    if os.path.isdir(tdir):
        print("  files:", os.listdir(tdir))
    print("  base exists:", os.path.isdir(base))
    if os.path.isdir(base):
        print("  base files:", os.listdir(base)[:30])
