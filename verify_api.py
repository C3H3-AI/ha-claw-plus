import urllib.request, json

URL = "http://localhost:8123/api/claw_plus/dashboard"

def post(d):
    req = urllib.request.Request(
        URL,
        data=json.dumps(d).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    return json.loads(urllib.request.urlopen(req).read())

actions = ["get_skills", "get_plugins", "get_docs", "get_mappings",
           "get_mapping_options", "get_option_schema", "read"]

for a in actions:
    try:
        r = post({"action": a})
        if not isinstance(r, dict):
            print(a, "->", type(r), r)
            continue
        if a == "get_option_schema":
            print(a, "ok" if r.get("ok") else "FAIL", "| sections:",
                  list(r.get("sections", {}).keys()),
                  "| agents:", len(r.get("agents", [])))
        elif a == "read":
            print(a, "ok" if r.get("ok") else "FAIL",
                  "| online:", r.get("online"), "| skills:", len(r.get("skills", [])))
        else:
            key = "skills" if "skills" in r else ("plugins" if "plugins" in r
                  else ("docs" if "docs" in r else ("mappings" if "mappings" in r else "options")))
            print(a, "ok" if r.get("ok") else "FAIL", "| count:", len(r.get(key, [])))
    except Exception as e:
        print(a, "ERR", repr(e))
