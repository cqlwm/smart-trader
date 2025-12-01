import json
from enum import Enum
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
    with open(path, 'w') as file:
        json.dump(obj, file, default=custom_serializer)
        file.write('\n')

def loads(s: str) -> Any:
    return json.loads(s)
