from pathlib import Path
import json,hashlib
root=Path("assets")
assets={}
for p in root.rglob("*.json"):
    if "schemas" in p.parts: continue
    try:data=json.loads(p.read_text(encoding="utf-8"))
    except: continue
    if isinstance(data,dict) and "id" in data: assets[data["id"]]=data
for aid,a in assets.items():
    if a.get("type")=="creature":
        assert a.get("drop_table") in assets
        for s in a.get("skills",[]): assert s in assets
print("VALIDATION PASS",len(assets))
