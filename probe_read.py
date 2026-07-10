import urllib.request, json

URL = "http://localhost:8123/api/claw_plus/dashboard"

def post(d):
    req = urllib.request.Request(URL, data=json.dumps(d).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(req).read())

r = post({"action": "read"})
print("TOP KEYS:", list(r.keys()))
print("has attrs:", "attrs" in r, "| attrs type:", type(r.get("attrs")))
print("attrs keys:", list((r.get("attrs") or {}).keys()))
print("skills len:", len(r.get("skills", [])), "| docs:", len(r.get("docs", [])),
      "| plugins:", len(r.get("plugins", [])), "| mappings:", len(r.get("user_mappings", [])))
print("primary_agent:", r.get("attrs", {}).get("primary_agent"))
print("online present:", "online" in r.get("attrs", {}))
# also get_option_schema menu_tree shape
s = post({"action": "get_option_schema"})
print("\nSCHEMA keys:", list(s.keys()))
mt = s.get("menu_tree", {})
print("menu_tree children:", list((mt.get("children") or {}).keys()))
