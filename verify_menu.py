import json, urllib.request

def post(action, data=None):
    body = {"action": action}
    if data:
        body.update(data)
    req = urllib.request.Request(
        "http://localhost:8123/api/claw_plus/dashboard",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req, timeout=15).read())

try:
    r = post("get_option_schema")
    mt = r.get("menu_tree", {})
    kids = mt.get("children", {})
    print("ok:", r.get("ok"))
    print("trans_found:", r.get("_debug_trans_found"))
    print("trans_path:", r.get("_debug_trans_path"))
    print("menu_tree.children count:", len(kids))
    print("tabs:", list(kids.keys()))
    for k, v in kids.items():
        sub = list((v.get("children") or {}).keys())
        print(f"  {k}: title={v.get('title')!r} sub={sub}")
    print("option_types:", len(r.get("option_types", {})))
    print("sections:", list(r.get("sections", {}).keys()))
except Exception as e:
    print("ERROR:", e)
