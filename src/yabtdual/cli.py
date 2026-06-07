import argparse
import configparser
import os
import shutil
import struct
import sys
from datetime import datetime, timezone
from typing import Any

# python-registry imports
from Registry import Registry


class WindowsRegistryLoader:
    def __init__(self, hive_path: str):
        self.hive_path = hive_path
        if not os.path.exists(hive_path):
            raise FileNotFoundError(f"Windows Registry file not found at: {hive_path}")

        print(f"[*] Loading Windows Registry Hive: {hive_path}")
        self.registry = Registry.Registry(hive_path)

    def get_bluetooth_keys(self) -> dict[str, dict[str, Any]]:
        """
        Extracts Bluetooth keys from the registry.
        Returns a dict: {Adapter_MAC: {Device_MAC: {KeyData...}}}
        """
        key_path = r"ControlSet001\Services\BTHPORT\Parameters\Keys"

        try:
            keys_key = self.registry.open(key_path)
        except Registry.RegistryKeyNotFoundException:
            print(f"[!] Path {key_path} not found. Trying variations...")
            return {}

        found_data = {}
        print("[*] Found BTHPORT Keys section.")

        for adapter in keys_key.subkeys():
            adapter_mac = adapter.name().lower()
            found_data[adapter_mac] = {}

            for device in adapter.subkeys():
                device_mac = device.name().lower()
                keys = {}
                for value in device.values():
                    keys[value.name()] = value.value()
                # Windows Registry stores timestamps in UTC.
                # python-registry returns naive datetime objects (implicitly UTC).
                # We set tzinfo=timezone.utc to make it aware, then convert to local system time.
                keys["__last_write__"] = (
                    device.timestamp().replace(tzinfo=timezone.utc).astimezone()
                )
                found_data[adapter_mac][device_mac] = keys

            for value in adapter.values():
                v_name = value.name().lower()
                if len(v_name) == 12:
                    if v_name not in found_data[adapter_mac]:
                        found_data[adapter_mac][v_name] = {
                            "LinkKey": value.value(),
                            "__type__": "Legacy_Direct",
                            "__last_write__": adapter.timestamp()
                            .replace(tzinfo=timezone.utc)
                            .astimezone(),
                        }
        return found_data


class LinuxConfigManager:
    def __init__(self, bluetooth_path: str = "/var/lib/bluetooth"):
        self.base_path = bluetooth_path
        if not os.path.exists(self.base_path):
            print(f"[!] Warning: Linux Bluetooth path {self.base_path} does not exist.")

    def get_adapters(self) -> list[str]:
        if not os.path.exists(self.base_path):
            return []
        return [
            d
            for d in os.listdir(self.base_path)
            if os.path.isdir(os.path.join(self.base_path, d)) and ":" in d
        ]

    def get_devices(self, adapter_mac: str) -> list[str]:
        adapter_path = os.path.join(self.base_path, adapter_mac)
        if not os.path.exists(adapter_path):
            return []
        return [
            d
            for d in os.listdir(adapter_path)
            if os.path.isdir(os.path.join(adapter_path, d)) and ":" in d
        ]

    def normalize_mac(self, mac_str: str) -> str:
        return mac_str.replace(":", "").lower()

    def backup_config(self, adapter_mac: str, device_mac: str):
        info_path = os.path.join(self.base_path, adapter_mac, device_mac, "info")
        if os.path.exists(info_path):
            ts = int(datetime.now().timestamp())
            backup_path = f"{info_path}.bak.{ts}"
            print(f"[*] Backing up {info_path} -> {backup_path}")
            shutil.copy2(info_path, backup_path)

    def get_device_name(self, adapter_mac: str, device_mac: str) -> str:
        """Reads the 'Name' or 'Alias' from the [General] section of the info file."""
        path = os.path.join(self.base_path, adapter_mac, device_mac, "info")
        if not os.path.exists(path):
            return "Unknown"

        try:
            config = configparser.ConfigParser()
            config.read(path)
            if "General" in config:
                return config["General"].get(
                    "Alias", config["General"].get("Name", "Unknown")
                )
        except Exception:
            pass
        return "Unknown"

    def format_mac_linux(self, mac_raw: str) -> str:
        """Converts aabbccddeeff to AA:BB:CC:DD:EE:FF"""
        m = mac_raw.replace(":", "").upper()
        return ":".join(m[i : i + 2] for i in range(0, 12, 2))

    def update_device_keys(
        self,
        adapter_mac: str,
        device_mac: str,
        win_keys: dict[str, Any],
        win_device_mac: str | None = None,
        dry_run: bool = False,
    ):
        # Target paths
        paths = [os.path.join(self.base_path, adapter_mac, device_mac, "info")]

        # If we have a divergent Windows MAC, we also want to create/update that file.
        # This handles devices that rotate MACs or use random addresses on Windows.
        if win_device_mac:
            norm_win = self.normalize_mac(win_device_mac)
            norm_linux = self.normalize_mac(device_mac)
            if norm_win != norm_linux:
                linux_style_win_mac = self.format_mac_linux(norm_win)
                win_path = os.path.join(
                    self.base_path, adapter_mac, linux_style_win_mac, "info"
                )
                paths.append(win_path)
                print(f"[*] Detected MAC divergence. Will also update: {win_path}")

        # Prepare data to write
        sections_to_write = {}

        # 1. Classic Link Key
        if "LinkKey" in win_keys:
            key_hex = win_keys["LinkKey"].hex().upper()
            sections_to_write["LinkKey"] = {
                "Key": key_hex,
                "Type": "4",
                "PINLength": "0",
            }

        # 2. BLE LTK
        if "LTK" in win_keys:
            ltk_hex = win_keys["LTK"].hex().upper()
            ediv = 0
            rand = 0
            if "EDIV" in win_keys:
                try:
                    ediv = (
                        win_keys["EDIV"]
                        if isinstance(win_keys["EDIV"], int)
                        else struct.unpack("<I", win_keys["EDIV"])[0]
                    )
                except Exception:
                    pass
            if "ERand" in win_keys:
                try:
                    rand = (
                        win_keys["ERand"]
                        if isinstance(win_keys["ERand"], int)
                        else struct.unpack("<Q", win_keys["ERand"])[0]
                    )
                except Exception:
                    pass

            sections_to_write["LongTermKey"] = {
                "Key": ltk_hex,
                "Authenticated": "1",
                "EncSize": "16",
                "EDiv": str(ediv),
                "Rand": str(rand),
            }
            # Some devices set themselves as master and linux as slave during pairing.
            # To ensure compatibility, we also write PeripheralLongTermKey and SlaveLongTermKey.
            sections_to_write["PeripheralLongTermKey"] = sections_to_write[
                "LongTermKey"
            ]
            sections_to_write["SlaveLongTermKey"] = sections_to_write["LongTermKey"]

        # 3. BLE IRK
        if "IRK" in win_keys:
            irk_hex = win_keys["IRK"].hex().upper()
            sections_to_write["IdentityResolvingKey"] = {"Key": irk_hex}

        # 4. BLE CSRK (Local)
        if "CSRK" in win_keys:
            csrk_hex = win_keys["CSRK"].hex().upper()
            sections_to_write["LocalSignatureKey"] = {
                "Key": csrk_hex,
                "Counter": "0",
                "Authenticated": "1",
            }

        # 5. BLE CSRK Inbound (Remote)
        if "CSRKInbound" in win_keys:
            csrk_in_hex = win_keys["CSRKInbound"].hex().upper()
            sections_to_write["RemoteSignatureKey"] = {
                "Key": csrk_in_hex,
                "Counter": "0",
                "Authenticated": "1",
            }

        # Execute updates
        original_path = paths[0]
        for path in paths:
            if dry_run:
                print(f"[*] [DRY RUN] Proposed config for {path}:")
            else:
                print(f"[*] Updating config at {path}")
                # Ensure directory exists (important for the secondary file)
                os.makedirs(os.path.dirname(path), exist_ok=True)

                # Backup if exists
                if os.path.exists(path):
                    # We reuse backup_config logic but need to adapt it since it takes macs.
                    # Instead, let's just do a simple copy here to avoid re-parsing paths.
                    ts = int(datetime.now().timestamp())
                    backup_path = f"{path}.bak.{ts}"
                    print(f"[*] Backing up {path} -> {backup_path}")
                    shutil.copy2(path, backup_path)
                elif path != original_path and os.path.exists(original_path):
                    # Copy original if creating new dual-entry to preserve metadata/permissions
                    print(f"[*] Copying base config from {original_path} to {path}")
                    shutil.copy2(original_path, path)

            config = configparser.ConfigParser()
            config.optionxform = str

            try:
                # If file exists, read it to preserve other settings (like Name, Alias)
                # For new file (dry-run), try reading from original if available
                if os.path.exists(path):
                    config.read(path)
                elif path != original_path and os.path.exists(original_path):
                    config.read(original_path)

                for sec_name, values in sections_to_write.items():
                    if not config.has_section(sec_name):
                        config.add_section(sec_name)
                    for k, v in values.items():
                        config.set(sec_name, k, v)

                if dry_run:
                    import io

                    out = io.StringIO()
                    config.write(out, space_around_delimiters=False)
                    print("-" * 40)
                    print(out.getvalue())
                    print("-" * 40)
                else:
                    with open(path, "w") as f:
                        config.write(f, space_around_delimiters=False)
                    print(f"[*] Successfully updated {path}")
            except Exception as e:
                print(f"[!] Error processing config {path}: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="yabtdual - Yet Another Bluetooth Dualboot Key Sync"
    )
    parser.add_argument(
        "--win-path", required=True, help="Path to Windows SYSTEM registry hive"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print changes without writing to disk"
    )
    args = parser.parse_args()

    try:
        win_loader = WindowsRegistryLoader(args.win_path)
        win_data = win_loader.get_bluetooth_keys()
    except Exception as e:
        print(f"[!] Error loading registry: {e}")
        sys.exit(1)

    if not win_data:
        print("[!] No Bluetooth keys found in the provided registry hive.")
        sys.exit(1)

    linux_mgr = LinuxConfigManager()
    adapters = linux_mgr.get_adapters()

    if not adapters:
        print(
            "[!] No Bluetooth adapters found in Linux (/var/lib/bluetooth is empty or missing)."
        )
        sys.exit(1)

    candidates = []

    print("\n[*] Scanning for matchable devices...")
    for adapter in adapters:
        norm_adapter = linux_mgr.normalize_mac(adapter)

        win_adapter_keys = win_data.get(norm_adapter)
        if not win_adapter_keys:
            continue

        devices = linux_mgr.get_devices(adapter)
        for device in devices:
            norm_device = linux_mgr.normalize_mac(device)
            dev_name = linux_mgr.get_device_name(adapter, device)

            target_keys = None
            win_mac = None
            match_type = ""
            match_info = ""

            # A. Direct Match
            if norm_device in win_adapter_keys:
                target_keys = win_adapter_keys[norm_device]
                win_mac = norm_device
                match_type = "DIRECT"
                ts = target_keys.get("__last_write__")
                match_info = (
                    f"Last Modified: {ts.strftime('%Y-%m-%d %H:%M')}"
                    if ts
                    else "Unknown"
                )
            else:
                # B. Heuristic Match
                best_candidate = None
                latest_ts = datetime.min.replace(
                    tzinfo=datetime.now().astimezone().tzinfo
                )
                for w_dev, w_data in win_adapter_keys.items():
                    if "__last_write__" in w_data:
                        ts = w_data["__last_write__"]
                        if ts > latest_ts:
                            latest_ts = ts
                            best_candidate = (w_dev, w_data)

                if best_candidate:
                    cand_mac, cand_data = best_candidate
                    target_keys = cand_data
                    win_mac = cand_mac
                    match_type = "HEURISTIC"
                    match_info = (
                        f"Mapped to Win Dev {cand_mac}"
                        f" ({latest_ts.strftime('%Y-%m-%d %H:%M')})"
                    )

            if target_keys:
                candidates.append(
                    {
                        "adapter": adapter,
                        "device": device,
                        "name": dev_name,
                        "target_keys": target_keys,
                        "win_mac": win_mac,
                        "match_type": match_type,
                        "match_info": match_info,
                    }
                )

    if not candidates:
        print("[!] No matching devices found to sync.")
        sys.exit(0)

    print("\n" + "=" * 100)
    print(
        f"{'ID':<4} | {'Device MAC':<18} | {'Device Name':<20} | {'Match Type':<10} | {'Info':<40}"
    )
    print("-" * 100)

    for idx, c in enumerate(candidates):
        # Truncate name if too long
        name_display = (c["name"][:17] + "..") if len(c["name"]) > 19 else c["name"]
        print(
            f"{idx + 1:<4} | {c['device']:<18} | {name_display:<20}"
            f" | {c['match_type']:<10} | {c['match_info']}"
        )
    print("=" * 100)

    print(
        "\nEnter IDs to sync (comma separated, e.g. '1,3') or 'all'. Press Enter to cancel."
    )
    user_input = input("> ").strip().lower()

    if not user_input:
        print("Cancelled.")
        sys.exit(0)

    selected_indices = []
    if user_input == "all":
        selected_indices = range(len(candidates))
    else:
        try:
            parts = user_input.split(",")
            for p in parts:
                val = int(p.strip())
                if 1 <= val <= len(candidates):
                    selected_indices.append(val - 1)
                else:
                    print(f"[!] Invalid ID ignored: {val}")
        except ValueError:
            print("[!] Invalid input format.")
            sys.exit(1)

    if not selected_indices:
        print("No valid devices selected.")
        sys.exit(0)

    print(f"\n[*] Syncing {len(selected_indices)} devices...")
    for idx in selected_indices:
        cand = candidates[idx]
        print(f"\n[+] Processing {cand['device']}...")
        linux_mgr.update_device_keys(
            cand["adapter"],
            cand["device"],
            cand["target_keys"],
            win_device_mac=cand["win_mac"],
            dry_run=args.dry_run,
        )

    print(
        "\n[*] Done. Please restart bluetooth service"
        " (systemctl restart bluetooth) to apply changes."
    )


if __name__ == "__main__":
    main()
