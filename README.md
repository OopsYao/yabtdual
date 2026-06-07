# yabtdual (Yet Another Bluetooth Dualboot)

## 📖 Introduction
**yabtdual** is a Python automation tool designed to solve the problem of repeatedly pairing Bluetooth devices on Windows and Linux dual-boot systems.

Typically, when a Bluetooth device (e.g., keyboard, mouse, headset) pairs with a computer, a unique Link Key is generated. If you pair on OS A, then boot into OS B and pair again, the device generates a new Link Key, invalidating the old key on OS A.

This tool **extracts the latest keys from the Windows registry and syncs them to Linux BlueZ configuration files**, allowing seamless switching between the two systems without re-pairing.

---

## 🛠️ Technical Scheme

### 1. Core Logic
Linux (BlueZ) configuration files have a clear structure and can be modified with root privileges, while Windows registry permissions are extremely restrictive. Therefore, the best practice is:
1. Pair on Linux (generates Key A, temporarily valid).
2. Pair on Windows (generates Key B, written to device, Linux Key A becomes invalid).
3. Return to Linux, read the Windows registry hive, extract Key B.
4. Write Key B into the Linux configuration file.
5. Restart the Linux Bluetooth service to complete the sync.

### 2. Data Parsing

#### Windows Side (Source)
- **File path**: `C:\Windows\System32\config\SYSTEM` (Registry Hive)
- **Registry path**: `HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Keys`
- **Data structure**:
  - **Classic Bluetooth**: `LinkKey` stored directly (REG_BINARY).
  - **BLE (Low Energy)**: Contains multiple sub-keys, typically including:
    - `LTK` (Long Term Key)
    - `IRK` (Identity Resolving Key)
    - `CSRK` (Connection Signature Resolving Key)
    - `CSRKInbound` (Inbound CSRK)
    - `EDIV` (Encrypted Diversifier) & `ERand` (Random Number)

#### Linux Side (Target)
- **File path**: `/var/lib/bluetooth/<Adapter MAC>/<Device MAC>/info`
- **Data format** (INI-like):
  ```ini
  [LinkKey]
  Key=B90F4D... (Classic Bluetooth)
  Type=4

  [LongTermKey]
  Key=... (BLE LTK)
  Authenticated=...
  EncSize=...
  EDiv=...
  Rand=...

  [PeripheralLongTermKey]
  # Some devices set themselves as Master during pairing; when Linux acts as Slave, this field is needed
  Key=... (same as LongTermKey)
  Authenticated=...
  EncSize=...
  EDiv=...
  Rand=...

  [SlaveLongTermKey]
  # Compatibility fallback (older or specific devices may use this name)
  Key=... (same as LongTermKey)
  Authenticated=...
  EncSize=...
  EDiv=...
  Rand=...

  [IdentityResolvingKey]
  Key=... (BLE IRK)

  [LocalSignatureKey]
  Key=... (BLE CSRK)
  Counter=0
  Authenticated=1

  [RemoteSignatureKey]
  Key=... (BLE CSRKInbound)
  Counter=0
  Authenticated=1
  ```

### 3. Matching Strategy
Since BLE devices may use random addresses, the MAC addresses recorded by Windows and Linux may have minor differences or formatting inconsistencies.
1. **Direct Match**:
   - Convert the Linux directory name (Identity Address) to uppercase without colons, and look it up directly in the Windows registry.
2. **Heuristic Match (Fallback)**:
   - If a direct match fails, the tool initiates a heuristic search:
     - **Timestamp analysis**: Scans the last write time of all devices under the adapter in the Windows registry, automatically correlating recently paired/connected devices.
3. **Data Sync & Dual Write**:
   - After a successful match, all security parameters (`LTK`, `IRK`, `CSRK`, `CSRKInbound`, `EDiv`, `Rand`) are synced.
   - **Dual Write Strategy**: When the Windows registry index (MAC address) differs from the Linux-recorded MAC, the tool applies a compatibility strategy:
     - Updates the original Linux config file associated with its MAC address.
     - **Additionally generates** a config file named after the Windows registry MAC.
     - *Rationale*: Some devices use random MACs under Windows, or reset their MAC on each pairing. Dual writing ensures that the correct keys are found regardless of which MAC address Linux uses. Users can remove the invalid device record via the Bluetooth manager after syncing.

### 4. Features
- [x] **Interactive Selection**: Scans all potential matches, lists devices for user selection to prevent accidental operations.
- [x] **Dry Run Mode**: Preview changes without writing to files.
- [x] **Auto-mount Detection**: Automatically attempts to find and read the Windows `SYSTEM` hive file.
- [x] **Multi-device Support**: Handles both Classic Bluetooth (Link Key) and BLE (LTK/IRK/CSRK/CSRKInbound).
- [x] **MAC Address Normalization**: Automatically handles MAC address format conversion between Windows (no separators) and Linux (colon-separated).

---

## 🚀 Quick Start

### Dependencies
Requires Python 3.10+ and a library for parsing registry hives.
```bash
# Using uv (recommended, local development)
uv sync

# Or install directly via tool (no manual clone needed)
uv tool install git+https://github.com/<user>/yabtdual
```

### Installation

```bash
# Install from GitHub (recommended)
uv tool install git+https://github.com/<user>/yabtdual

# Or using pipx
pipx install git+https://github.com/<user>/yabtdual

# Local development
uv sync
```

### Usage
*Note: Reading the system partition and modifying system configuration requires `sudo` permissions for actual write operations.*

#### 1. Run the tool
```bash
# Standard mode (after package install)
sudo yabtdual --win-path /mnt/c/Windows/System32/config/SYSTEM

# Preview only (Dry Run)
sudo yabtdual --win-path /mnt/c/Windows/System32/config/SYSTEM --dry-run

# Local development mode
sudo uv run yabtdual --win-path /mnt/c/Windows/System32/config/SYSTEM
```

#### 2. Interactive Flow
The tool lists all matched Bluetooth devices:
```text
ID   | Device MAC         | Device Name          | Match Type | Info
----------------------------------------------------------------------------------------------------
1    | AA:BB:CC:DD:EE:FF  | Logitech MX Mast..   | DIRECT     | Last Modified: 2023-10-27 18:00
2    | 11:22:33:44:55:66  | WH-1000XM4           | HEURISTIC  | Mapped to Win Dev 1122... (2023-10-27 18:05)
====================================================================================================
Enter IDs to sync (comma separated, e.g. '1,3') or 'all'. Press Enter to cancel.
> 1
```
Enter the corresponding ID to start syncing. The tool automatically backs up the original config file and injects the new keys.

#### 3. Restart Service
After syncing, restart the Bluetooth service to apply changes:
```bash
sudo systemctl restart bluetooth
```

---

## ⚠️ Disclaimer
- This tool modifies system-level configuration files. Although it includes backup functionality, use with caution.
- Some premium Bluetooth devices (e.g., Logitech Flow series) have built-in multi-device switching and do not require this tool.
- Some newer Windows versions may store keys in encrypted form, requiring additional decryption steps (pending implementation).
- **Time Display**: The tool assumes Windows system time is set to UTC (RealTimeIsUniversal=1), or Linux is correctly configured to handle Windows' local time storage. If the two are inconsistent, the displayed "last modified time" may be off, but this does not affect key synchronization.