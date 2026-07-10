import urllib.request, json, time

BASE = "http://localhost:8123"

def call(path, data=None):
    url = BASE + path
    if data is not None:
        req = urllib.request.Request(url, data=json.dumps(data).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
    else:
        req = urllib.request.Request(url)
    return urllib.request.urlopen(req, timeout=6)

# 等待 HA 启动
up = False
for i in range(42):
    try:
        if call("/api/").status == 200:
            up = True
            print(f"HA_UP after ~{i*15}s")
            break
    except Exception:
        pass
    time.sleep(15)

if not up:
    print("HA_NOT_UP")
else:
    # 1) GET 头部 + 内容
    r = call("/api/claw_plus/dashboard")
    html = r.read().decode("utf-8")
    print("GET_STATUS:", r.status)
    print("CACHE_CONTROL:", r.headers.get("Cache-Control"))
    print("HTML_BYTES:", len(html))
    print("HAS_FIX_d.attrs||d:", "d.attrs || d" in html)
    # 2) schema
    r2 = call("/api/claw_plus/dashboard", {"action": "get_option_schema"})
    d2 = json.loads(r2.read())
    print("SCHEMA_OK:", d2.get("ok"), "| SECTIONS:", list(d2.get("sections", {}).keys()))
    # 3) read 结构
    r3 = call("/api/claw_plus/dashboard", {"action": "read"})
    d3 = json.loads(r3.read())
    print("READ_KEYS:", list(d3.keys())[:8], "| has_attrs:", "attrs" in d3)
    # 4) 实时列表
    for a in ["get_skills", "get_plugins", "get_docs", "get_mappings"]:
        r4 = call("/api/claw_plus/dashboard", {"action": a})
        d4 = json.loads(r4.read())
        cnt = len(d4.get("skills", d4.get("plugins", d4.get("docs", d4.get("mappings", [])))))
        print(f"{a}: ok={d4.get('ok')} count={cnt}")
print("DONE")
