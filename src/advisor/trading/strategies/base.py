from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Optional

import pandas as pd

from advisor.trading.types import PositionState, StrategySignal


@dataclass(slots=True)
class StrategyContext:
    symbol: str
    index: int
    data: pd.DataFrame
    state: Dict[str, object]

    @property
    def row(self) -> pd.Series:
        return self.data.iloc[self.index]


class BaseStrategy(ABC):
    name: str

    @abstractmethod
    def prepare_features(self, data: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError

    @abstractmethod
    def generate_entry(self, context: StrategyContext) -> StrategySignal:
        raise NotImplementedError

    @abstractmethod
    def generate_exit(self, context: StrategyContext, position: PositionState) -> StrategySignal:
        raise NotImplementedError

    @abstractmethod
    def validate_entry(self, context: StrategyContext) -> bool:
        raise NotImplementedError

    def generate_signal(self, context: StrategyContext) -> StrategySignal:
        return self.generate_entry(context)
