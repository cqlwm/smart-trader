import json
from enum import Enum


def custom_serializer(obj):
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, '__dict__'):
        return obj.__dict__
    raise TypeError(f"Object of type {obj.__class__.__name__} is not JSON serializable")


def dumps(obj):
    return json.dumps(obj, default=custom_serializer)


def dump_file(obj, path):
    with open(path, 'w') as file:
        json.dump(obj, file, default=custom_serializer)


def loads(s):
    return json.loads(s)
