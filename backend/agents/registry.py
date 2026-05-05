"""직원 모듈 레지스트리. builtins/*.py 를 자동 스캔하여 등록."""
import importlib
import pkgutil
from typing import Type

from .base import AgentModule

_REGISTRY: dict[str, Type[AgentModule]] = {}


def register(cls: Type[AgentModule]) -> Type[AgentModule]:
    """데코레이터. 클래스에 붙이면 자동 등록."""
    if not getattr(cls, "slug", ""):
        raise ValueError(f"{cls.__name__} has no slug")
    _REGISTRY[cls.slug] = cls
    return cls


def get(slug: str) -> Type[AgentModule] | None:
    return _REGISTRY.get(slug)


def all_modules() -> list[Type[AgentModule]]:
    return list(_REGISTRY.values())


def load_builtins() -> None:
    """backend/agents/builtins/ 안의 모든 모듈을 import → @register가 발동."""
    from . import builtins as pkg

    for _, name, _ in pkgutil.iter_modules(pkg.__path__):
        importlib.import_module(f"{pkg.__name__}.{name}")
