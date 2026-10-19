"""Persist per-user departure places and return fresh mobile card data."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid


class MemoryError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(code)
        self.code = code
        self.message = message


def user_key(user_id):
    if not isinstance(user_id, str) or not user_id.strip():
        raise MemoryError("invalid_user", "需要宿主提供的当前用户标识。")
    return hashlib.sha256(user_id.strip().encode("utf-8")).hexdigest()


def profile_path(data_dir, user_id):
    return Path(data_dir).resolve() / "users" / user_key(user_id) / "profile.json"


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise MemoryError("invalid_" + field, "地点名称和具体位置不能留空。")
    return value.strip()


def _validate(profile, owner):
    try:
        if profile["schema_version"] != 2 or profile["owner_key"] != owner:
            raise ValueError("profile ownership or schema mismatch")
        data = profile["origin_memory"]
        if type(data["revision"]) is not int or data["revision"] < 0 or not isinstance(data["locations"], list):
            raise ValueError("invalid memory state")
        ids, labels = set(), set()
        for item in data["locations"]:
            key = _text(item["id"], "id")
            label = _text(item["label"], "label").casefold()
            _text(item["address"], "address")
            if key in ids or label in labels:
                raise ValueError("duplicate memory entry")
            ids.add(key)
            labels.add(label)
    except (ValueError, KeyError, TypeError, MemoryError):
        raise MemoryError("invalid_profile", "用户配置结构或归属异常，请先核对；未覆盖原文件。") from None


def _load(path, owner):
    try:
        if path.exists():
            profile = json.loads(path.read_text(encoding="utf-8"))
        else:
            template = Path(__file__).resolve().parents[1] / "assets" / "profile.example.json"
            profile = json.loads(template.read_text(encoding="utf-8"))
            profile["owner_key"] = owner
    except (OSError, UnicodeError, ValueError, TypeError):
        raise MemoryError("invalid_profile", "无法读取用户配置；未覆盖原文件。") from None
    _validate(profile, owner)
    return profile


def _snapshot(profile, status="listed", origin=None):
    data = profile["origin_memory"]
    locations = deepcopy(data["locations"])
    options = [dict(id=item["id"], origin_id=item["id"], label=item["label"], address=item["address"], action="select_origin") for item in locations]
    labels = {item["label"] for item in locations}
    for alias, key in (("家", "home"), ("公司", "company")):
        if alias not in labels:
            options.append(dict(id="unset-" + key, label=alias + "（未设置）", suggested_label=alias, action="enter_origin"))
    options.extend([
        dict(id="once", label="其他地点（仅本次）", action="enter_once"),
        dict(id="manage", label="管理常用地点", action="manage_origins"),
    ])
    result = dict(status=status, revision=data["revision"], locations=locations,
                  card=dict(title="这次从哪里出发？", revision=data["revision"], selected_id=None, options=options))
    if origin is not None:
        result["origin"] = deepcopy(origin)
    return result


def _write(path, profile):
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".profile-", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(profile, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        saved = json.loads(path.read_text(encoding="utf-8"))
        if saved != profile:
            raise ValueError("readback mismatch")
    except (OSError, UnicodeError, ValueError):
        raise MemoryError("save_failed", "保存或回读核对失败，请重新读取当前配置，暂不报告成功。") from None
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def execute(data_dir, user_id, action, *, label=None, address=None, origin_id=None, expected_revision=None):
    owner = user_key(user_id)
    path = profile_path(data_dir, user_id)
    if action not in {"list", "add", "update", "delete"}:
        raise MemoryError("invalid_action", "不支持的地点操作。")
    if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 0):
        raise MemoryError("invalid_revision", "卡片版本无效，请刷新后重试。")
    if action == "list":
        return _snapshot(_load(path, owner))
    if action == "add" and (address is None or not str(address).strip()):
        result = _snapshot(_load(path, owner), "needs_input")
        result["form"] = dict(title="新增常用出发地点", required=["address"], values=dict(label=label or "", address=""))
        return result

    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise MemoryError("memory_busy", "另一个地点操作正在保存，请稍后刷新卡片。") from None
    except OSError:
        raise MemoryError("save_failed", "无法取得配置写入权限，未保存。") from None
    try:
        profile = _load(path, owner)
        data = profile["origin_memory"]
        if expected_revision is not None and expected_revision != data["revision"]:
            raise MemoryError("stale_revision", "地点列表已经更新，请刷新卡片后重新选择。")
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if action == "add":
            clean_address = _text(address, "address")
            clean_label = _text(label if label is not None else clean_address, "label")
            existing = next((item for item in data["locations"] if item["label"].casefold() == clean_label.casefold()), None)
            if existing:
                if existing["address"] == clean_address:
                    return _snapshot(profile, "unchanged", existing)
                raise MemoryError("duplicate_label", "已有同名地点，请选择修改原条目或使用不同名称。")
            item = dict(id=str(uuid.uuid4()), label=clean_label, address=clean_address, created_at=now, updated_at=now)
            data["locations"].append(item)
        else:
            item = next((item for item in data["locations"] if item["id"] == origin_id), None)
            if item is None:
                raise MemoryError("not_found", "该地点不存在，请刷新地点列表。")
            if action == "delete":
                data["locations"].remove(item)
            else:
                new_label = _text(label, "label") if label is not None else item["label"]
                new_address = _text(address, "address") if address is not None else item["address"]
                if any(other["id"] != item["id"] and other["label"].casefold() == new_label.casefold() for other in data["locations"]):
                    raise MemoryError("duplicate_label", "已有同名地点，请使用不同名称。")
                if (new_label, new_address) == (item["label"], item["address"]):
                    return _snapshot(profile, "unchanged", item)
                item.update(label=new_label, address=new_address, updated_at=now)
        data["revision"] += 1
        _validate(profile, owner)
        _write(path, profile)
        return _snapshot(profile, "deleted" if action == "delete" else "saved", item)
    finally:
        os.close(lock_fd)
        lock_path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", default=".travel-assistant")
    parser.add_argument("--user-id", required=True, help="Authenticated user identity supplied by the host")
    subparsers = parser.add_subparsers(dest="action", required=True)
    subparsers.add_parser("list")
    for action in ("add", "update", "delete"):
        command = subparsers.add_parser(action)
        command.add_argument("--expected-revision", type=int)
        if action != "add":
            command.add_argument("--origin-id", required=True)
        if action != "delete":
            command.add_argument("--label")
            command.add_argument("--address")
    arguments = vars(parser.parse_args())
    try:
        result = execute(**arguments)
    except MemoryError as error:
        print(json.dumps(dict(status="error", code=error.code, message=error.message), ensure_ascii=False))
        return 1
    except OSError:
        print(json.dumps(dict(status="error", code="io_failed", message="本地配置访问失败，请先检查读写权限。"), ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
