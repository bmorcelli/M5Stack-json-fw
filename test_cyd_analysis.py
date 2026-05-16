"""Diagnostic script: analyze a few CYD firmware URLs with full instrumentation."""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import requests as _requests
from script.firmware_manifest import RangeReader, parse_partition_table, analyze_remote_firmware, PARTITION_TYPE_APP

TEST_URLS = [
    # Bruce launcher - GitHub release asset
    ("Bruce (CYD S028R)", "1.14", "https://github.com/BruceDevices/firmware/releases/download/1.14/Bruce-LAUNCHER_CYD-2432S028.bin"),
    # NerdMiner - GitHub Pages
    ("NerdMiner", "S028R", "https://fr4nkfletcher.github.io/NerdMiner_v2-Cheap-Yellow-Display/web/ESP32-2432S028R_firmware.bin"),
    # Launcher - GitHub release
    ("Launcher (CYD S028R)", "2.6.10", "https://github.com/bmorcelli/Launcher/releases/download/2.6.10/Launcher-CYD-2432S028.bin"),
    # HaleHound - GitHub release
    ("HaleHound (CYD)", "v3.5.5", "https://github.com/JesseCHale/HaleHound-CYD/releases/download/v3.5.5/HaleHound-CYD-FULL.bin"),
]

session = _requests.Session()

for name, ver, url in TEST_URLS:
    print(f"\n{'='*70}")
    print(f"  {name} - {ver}")
    print(f"  {url}")
    print(f"{'='*70}")

    reader = RangeReader(url, session=session)
    reader.head()
    print(f"  HEAD => content_length={reader.content_length}, accept_ranges={reader.accept_ranges}")
    print(f"          etag={reader.etag}")

    try:
        first_bytes = reader.read_header_chunk(0x9000)
        print(f"  read_header_chunk(0x9000) => got {len(first_bytes)} bytes (content_length={reader.content_length})")
    except Exception as e:
        print(f"  read_header_chunk(0x9000) => ERROR: {e}")
        continue

    magic = first_bytes[0] if first_bytes else None
    print(f"  first_bytes[0] = 0x{magic:02X}" if magic is not None else "  first_bytes is empty")

    table_data = first_bytes[0x8000:0x9000] if len(first_bytes) >= 0x9000 else b""
    print(f"  table_data length = {len(table_data)} (from buffer; len(first_bytes)={len(first_bytes)})")

    if not table_data and reader.content_length > 0x8000:
        print(f"  => would need second range read for partition table (file large enough)")
    elif not table_data:
        print(f"  => file too small for partition table region (content_length={reader.content_length})")

    partitions = parse_partition_table(table_data, 0x8000)
    print(f"  parse_partition_table => {len(partitions)} entries")
    for p in partitions:
        print(f"    offset=0x{p['offset']:X} size=0x{p['size']:X} label={p['label']} type={p['type_id']}")

    if partitions and reader.content_length:
        app_parts = [p for p in partitions if p["type_id"] == PARTITION_TYPE_APP]
        if app_parts:
            app_off = int(app_parts[0]["offset"])
            print(f"  app_offset=0x{app_off:X} vs content_length={reader.content_length} => {'VALID' if app_off < reader.content_length else 'INVALID (would be discarded)'}")

    print(f"\n  --- Full analysis ---")
    version = {"version": ver, "file": url}
    item = {"name": name, "category": "CYD"}
    try:
        analyze_remote_firmware(version, item, session=session)
    except Exception as e:
        print(f"  analyze_remote_firmware => EXCEPTION: {e}")
        continue

    install = version.get("install", {})
    analysis = install.get("analysis", {}) if install else {}
    print(f"  method        = {analysis.get('method', 'N/A')}")
    print(f"  confidence    = {analysis.get('confidence', 'N/A')}")
    print(f"  bytes_dl      = {analysis.get('bytes_downloaded_for_analysis', 'N/A')}")
    print(f"  warnings      = {analysis.get('warnings', [])}")
    print(f"  invalid       = {version.get('invalid', False)}")
    if "app" in install:
        print(f"  app.source_offset    = 0x{install['app']['source_offset']:X}")
        print(f"  app.image_size       = {install['app']['image_size']}")
        print(f"  app.partition_size   = {install['app']['partition_size']}")

print(f"\n{'='*70}")
print("Done.")
