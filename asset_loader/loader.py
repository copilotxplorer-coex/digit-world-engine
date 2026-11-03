import json
from pathlib import Path
from .models import AssetRecord

class AssetLoader:
    def __init__(self):
        self.assets={}

    def load_directory(self, root):
        root=Path(root)
        for p in root.rglob("*.json"):
            data=json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(data,dict):
                continue
            aid=data.get("id")
            atype=data.get("type")
            if aid and atype:
                self.assets[aid]=AssetRecord(aid,atype,str(p),data)
        return self.assets

    def get(self, asset_id):
        return self.assets.get(asset_id)

    def exists(self, asset_id):
        return asset_id in self.assets

    def by_type(self, asset_type):
        return [a for a in self.assets.values() if a.asset_type==asset_type]
