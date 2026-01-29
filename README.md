# MyBtDual (Bluetooth Dual-Boot Sync Tool)

## 📖 简介 / Introduction
**MyBtDual** 是一个 Python 自动化工具，旨在解决 Windows 和 Linux 双系统下蓝牙设备需要反复配对的问题。

通常情况下，蓝牙设备（如键盘、鼠标、耳机）与电脑配对时会生成唯一的 Link Key。当你在 OS A 配对后，切换到 OS B 再次配对，设备会生成新的 Link Key，导致 OS A 上的旧 Key 失效。

本工具通过**提取 Windows 注册表中的最新密钥，同步到 Linux 的 BlueZ 配置文件中**，实现无需重新配对即可在两个系统间无缝切换。

---

## 🛠️ 技术方案 / Technical Scheme

### 1. 核心原理 (Core Logic)
Linux (BlueZ) 的配置文件结构清晰且易于通过 Root 权限修改，而 Windows 的注册表权限管理极其严格。因此，最佳实践是：
1. 在 Linux 配对（生成 Key A，暂时有效）。
2. 在 Windows 配对（生成 Key B，写入设备，Linux Key A 失效）。
3. 回到 Linux，读取 Windows 注册表文件，提取 Key B。
4. 将 Key B 写入 Linux 配置文件。
5. 重启 Linux 蓝牙服务，实现同步。

### 2. 数据源解析 (Data Parsing)

#### Windows 端 (Source)
- **文件路径**: `C:\Windows\System32\config\SYSTEM` (Registry Hive)
- **注册表路径**: `HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Keys`
- **数据结构**:
  - **经典蓝牙**: 直接存储 `LinkKey` (REG_BINARY)。
  - **BLE (低功耗)**: 包含多个子值的 Key，通常包括：
    - `LTK` (Long Term Key)
    - `IRK` (Identity Resolving Key)
    - `CSRK` (Connection Signature Resolving Key)
    - `CSRKInbound` (Inbound CSRK)
    - `EDIV` (Encrypted Diversifier) & `ERand` (Random Number)

#### Linux 端 (Target)
- **文件路径**: `/var/lib/bluetooth/<Adapter MAC>/<Device MAC>/info`
- **数据格式** (INI-like format):
  ```ini
  [LinkKey]
  Key=B90F4D... (经典蓝牙)
  Type=4
  
  [LongTermKey]
  Key=... (BLE LTK)
  Authenticated=...
  EncSize=...
  EDiv=...
  Rand=...

  [PeripheralLongTermKey]
  # 某些设备在配对时会将自己设为 Master，此时 Linux 作为 Slave 需要此项
  Key=... (与 LongTermKey 相同)
  Authenticated=...
  EncSize=...
  EDiv=...
  Rand=...

  [SlaveLongTermKey]
  # 兼容性备选项 (旧版或特定设备可能使用此名称)
  Key=... (与 LongTermKey 相同)
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

### 3. 关键策略：匹配算法 (Matching Strategy)
由于 BLE 设备可能使用随机地址，Windows 和 Linux 记录的 MAC 地址可能存在微小差异或格式不一。
1. **精确匹配 (Direct Match)**: 
   - 将 Linux 目录名（Identity Address）转换为大写无冒号格式，直接在 Windows 注册表中查找。
2. **启发式匹配 (Heuristic Match - Fallback)**:
   - 如果精确匹配失败，工具将启动启发式搜索：
     - **时间戳分析**: 扫描 Windows 注册表中该适配器下所有设备的最后修改时间（LastWrite Time），自动关联最近配对/连接的设备。
3. **数据同步与双重写入 (Data Sync & Dual Write)**:
   - 匹配成功后，同步 `LTK`, `IRK`, `CSRK`, `CSRKInbound`, `EDiv`, `Rand` 等所有安全参数。
   - **双重写入策略**: 当 Windows 注册表索引（MAC地址）与 Linux 记录的 MAC 地址不一致时，工具会采取兼容策略：
     - 更新 Linux 原有 MAC 地址对应的配置文件。
     - **额外生成**一个以 Windows 注册表 MAC 为名的配置文件。
     - *原因*: 某些设备在 Windows 下使用随机 MAC，或者设备本身每次配对重置 MAC。双重写入确保无论 Linux 使用哪个 MAC 地址都能找到正确的密钥。用户可在同步后通过蓝牙管理器删除无效的那个设备记录。

### 4. 功能特性 (Features)
- [x] **交互式选择**: 扫描所有潜在匹配项，列出设备列表供用户选择同步，防止误操作。
- [x] **Dry Run 模式**: 预览修改内容而不实际写入文件。
- [x] **自动挂载检测**: 自动尝试寻找并读取 Windows `SYSTEM` Hive 文件。
- [x] **多设备支持**: 同时处理经典蓝牙 (Link Key) 和低功耗蓝牙 (BLE - LTK/IRK/CSRK/CSRKInbound)。
- [x] **MAC 地址归一化**: 自动处理 Windows (无分隔符) 和 Linux (冒号分隔) 的 MAC 地址格式转换。

---

## 🚀 快速开始 / Quick Start

### 依赖 (Dependencies)
需要 Python 3.10+ 以及处理注册表 Hive 的库。
```bash
# 使用 uv (推荐)
uv sync
```

### 使用方法 (Usage)
*注意：由于需要读取系统分区和修改系统配置，实际写入操作需要 `sudo` 权限。*

#### 1. 运行工具
```bash
# 标准模式
sudo uv run python main.py --win-path /mnt/c/Windows/System32/config/SYSTEM

# 仅预览 (Dry Run)
uv run python main.py --win-path /mnt/c/Windows/System32/config/SYSTEM --dry-run
```

#### 2. 交互流程
工具运行后会列出所有匹配的蓝牙设备：
```text
ID   | Device MAC         | Device Name          | Match Type | Info
----------------------------------------------------------------------------------------------------
1    | AA:BB:CC:DD:EE:FF  | Logitech MX Mast..   | DIRECT     | Last Modified: 2023-10-27 18:00
2    | 11:22:33:44:55:66  | WH-1000XM4           | HEURISTIC  | Mapped to Win Dev 1122... (2023-10-27 18:05)
====================================================================================================
Enter IDs to sync (comma separated, e.g. '1,3') or 'all'. Press Enter to cancel.
> 1
```
输入对应 ID 即可开始同步。工具会自动备份原配置文件并注入新的密钥。

#### 3. 重启服务
同步完成后，重启蓝牙服务以生效：
```bash
sudo systemctl restart bluetooth
```

---

## ⚠️ 注意事项 / Disclaimer
- 本工具涉及修改系统级配置文件，尽管包含备份功能，请谨慎使用。
- 部分高端蓝牙设备（如 Logitech Flow 系列）可能自带多设备切换功能，无需使用此工具。
- 某些新的 Windows 版本可能将 Key 加密存储，需要额外的解密步骤（待实现）。
