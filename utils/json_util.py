import json
from enum import Enum
import os
from typing import Any

def custom_serializer(obj: Any):
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, '__dict__'):
        return obj.__dict__
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")

def dumps(obj: Any) -> str:
    return json.dumps(obj, default=custom_serializer)

def dump_file(obj: Any, path: str):
    if not os.path.exists(path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as file:
        json.dump(obj, file, default=custom_serializer)
        file.write('\n')

def loads(s: str) -> Any:
    return json.loads(s)
