#!/usr/bin/env python3
"""Analyze data-partition usage (SPIFFS/FAT/LittleFS) across the merged M5Stack + 3rd-party firmware catalogs."""

import json
from collections import Counter
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
M5STACK_PATH = BASE_DIR / "v2" / "all_device_firmware.json"
OTHER_PATH = BASE_DIR / "3rd" / "r" / "all_devices_firmware.json"


def load_firmwares(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def iter_partitions(firmware):
    for version in firmware.get("versions", []):
        install = version.get("install") or {}
        for partition in install.get("partitions", []):
            yield partition


def has_data_partition(firmware):
    for partition in iter_partitions(firmware):
        if partition.get("type") == "data" and (partition.get("copy_size") or 0) > 0:
            return True
    return False


def collect_labels(firmwares, subtype):
    total = Counter()
    with_copy_size = Counter()
    for firmware in firmwares:
        labels_seen = set()
        labels_seen_with_copy_size = set()
        for partition in iter_partitions(firmware):
            if partition.get("type") != "data" or partition.get("subtype") != subtype:
                continue
            label = partition.get("label", "")
            labels_seen.add(label)
            if (partition.get("copy_size") or 0) > 0:
                labels_seen_with_copy_size.add(label)
        for label in labels_seen:
            total[label] += 1
        for label in labels_seen_with_copy_size:
            with_copy_size[label] += 1
    return total, with_copy_size


def print_label_section(title, firmwares, subtype):
    print(f"- common labels for {title}: ")
    total, with_copy_size = collect_labels(firmwares, subtype)
    for label, count in total.most_common():
        print(f"  {label:<8} ({count}) ({with_copy_size.get(label, 0)})")


def main():
    m5stack = load_firmwares(M5STACK_PATH)
    other = load_firmwares(OTHER_PATH)
    all_firmwares = m5stack + other

    total_count = len(all_firmwares)
    m5stack_count = len(m5stack)
    other_count = len(other)
    needing_data_count = sum(1 for fw in all_firmwares if has_data_partition(fw))

    print(f"- Total of Firmwares available: {total_count}")
    print(f"- M5Stack: (v2) {m5stack_count}     Other: (3rd/r) {other_count}")
    print(f"- Number of firmware needing \"data\": {needing_data_count}")
    print_label_section("SPIFFS", all_firmwares, "spiffs")
    print_label_section("FAT", all_firmwares, "fat")
    print_label_section("LittleFS", all_firmwares, "littlefs")


if __name__ == "__main__":
    main()
