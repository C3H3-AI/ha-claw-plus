import json, urllib.request, sys

auth = json.load(open("/config/.storage/auth"))
token = None
for u in auth["data"]["users"].values():
    for rt in u.get("refresh_tokens", {}).values():
        if rt.get("access_token"):
            token = rt["access_token"]
            break
    if token:
        break

req = urllib.request.Request(
    "http://localhost:8123/api/claw_plus/dashboard",
    headers={"Authorization": f"Bearer {token}"},
)
try:
    r = urllib.request.urlopen(req, timeout=10)
    body = r.read().decode("utf-8", "replace")
    print("STATUS:", r.status, "LEN:", len(body))
    print(body[:500])
except Exception as e:
    print("ERROR:", type(e).__name__, str(e)[:400])
