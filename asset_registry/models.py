from dataclasses import dataclass
@dataclass
class AssetRecord:
    asset_id:str
    asset_type:str
    path:str
    dependencies:list
