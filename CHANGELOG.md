# Changelog

## [1.0.0] - 2026-06-07

- Sync Bluetooth keys from Windows to Linux by parsing the Windows `SYSTEM` registry hive and writing Link Keys, LTK, IRK, CSRK, EDiv/Rand into BlueZ config files.
- Interactive CLI with dry-run mode, automatic `.bak` backup, and dual-write fallback for mismatched MAC addresses across the two OSes.
