from dataclasses import dataclass
@dataclass
class Wallet: id:str; balance:float=0
@dataclass
class Transaction: id:str; amount:float
