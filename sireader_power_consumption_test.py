"""
PPK2 Source Meter - Automated Current Measurement Script
========================================================

Requirements:
    pip install ppk2-api pyserial numpy

Hardware:
    - Nordic Power Profiler Kit II connected via USB
    - PPK2 used in Source Meter mode

Usage:
     1. Connect PPK2 via USB.
     2. By default the script auto-detects the PPK2 port.  If auto-detection
         fails you can override it by setting SERIAL_PORT to the correct port
         (e.g. "COM7" on Windows or "/dev/ttyACM0" on Linux/macOS).
     3. Run:  python sireader_power_consumption_test.py
     4. Press ENTER to start a dual-phase measurement cycle:
         - Measure 20 seconds for BLE Advertisement mode
         - Wait 10 seconds for DUT transition to sleep (DUT power stays ON)
         - Measure 20 seconds for Sleep mode
     5. Press Ctrl+C to exit.

Results are printed to the console and appended to 'results.csv'.
"""

import csv
import time
from datetime import datetime

import numpy as np
from ppk2_api.ppk2_api import PPK2_API

# ---------------------------------------------------------------------------
# Configuration — edit these values before running
# ---------------------------------------------------------------------------
SERIAL_PORT = None             # None = auto-detect (recommended)
                               # Override if needed, e.g. "COM7" or "/dev/ttyACM0"
SOURCE_VOLTAGE_MV = 3600       # DUT supply voltage in Source Meter mode.
BLE_ADVERTISEMENT_DURATION_S = 20  # Duration to measure BLE advertisement mode (seconds)
TRANSITION_WAIT_S = 10            # Wait time between measurements (seconds)
SLEEP_MODE_DURATION_S = 20        # Duration to measure sleep mode (seconds)
CSV_FILE = "results.csv"       # Output CSV file (created if it does not exist)

# Pass/Fail criteria (in microamps)
BLE_ADVERTISEMENT_MIN_UA = 100         # Minimum acceptable BLE advertisement current
BLE_ADVERTISEMENT_MAX_UA = 120         # Maximum acceptable BLE advertisement current
SLEEP_MODE_MIN_UA = 1                  # Minimum acceptable sleep mode current
SLEEP_MODE_MAX_UA = 2                  # Maximum acceptable sleep mode current
# ---------------------------------------------------------------------------


class PPK2AmpMeter:
    """Wrap the PPK2 API for repeated Source Meter measurements.

    The class name is kept for compatibility with the original requirement,
    but the measurement flow now uses *Source Meter* mode. The PPK2 powers the
    DUT at a fixed voltage and measures the DUT current during each cycle.
    """

    def __init__(self, port: str | None = None, csv_file: str = CSV_FILE):
        self.port = port  # None triggers auto-detection in connect()
        self.csv_file = csv_file
        self.ppk2: PPK2_API | None = None
        self.test_number = 0
        self._ensure_csv_header()

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    @staticmethod
    def find_ppk2_port() -> str:
        """Auto-detect the PPK2 serial port.

        ``PPK2_API.list_devices()`` scans all COM ports and returns those
        whose USB Vendor-ID matches Nordic Semiconductor (0x1915).  When the
        PPK2 is connected it normally exposes two ports:

        * **nRF Connect USB CDC ACM** — the data port used by ppk2_api.
        * **Serielles USB-Gerät / USB Serial Device** — a secondary port.

        This method returns the CDC ACM port when both are detected, and
        falls back to the first Nordic port if the description cannot be
        matched.
        """
        from serial.tools import list_ports  # part of pyserial

        candidates: list[str] = PPK2_API.list_devices()
        if not candidates:
            raise ConnectionError(
                "No PPK2 device found. Check the USB cable and drivers."
            )

        if len(candidates) == 1:
            return candidates[0]

        # Multiple Nordic ports — prefer the CDC ACM data port
        port_info = {p.device: p for p in list_ports.comports()}
        for port in candidates:
            info = port_info.get(port)
            if info and "nrf connect" in (info.description or "").lower():
                return port

        # Fallback: first candidate
        return candidates[0]

    def connect(self) -> None:
        """Open the serial connection to the PPK2 and configure Source Meter mode.

        If *port* was not specified at construction time the port is
        auto-detected via :meth:`find_ppk2_port`.
        """
        if self.port is None:
            print("No serial port specified — scanning for PPK2 …")
            self.port = self.find_ppk2_port()
            print(f"PPK2 found on {self.port}.")

        print(f"Connecting to PPK2 on {self.port} …")
        try:
            self.ppk2 = PPK2_API(self.port, timeout=1)
            self.ppk2.get_modifiers()              # fetch calibration modifiers from device
            self.ppk2.use_source_meter()           # select Source Meter mode
            self.ppk2.set_source_voltage(SOURCE_VOLTAGE_MV)
            self.ppk2.toggle_DUT_power("OFF")
            print(
                f"Connected. PPK2 configured in Source Meter mode at {SOURCE_VOLTAGE_MV} mV."
            )
        except Exception as exc:
            raise ConnectionError(
                f"Failed to connect to PPK2 on {self.port}: {exc}"
            ) from exc

    def disconnect(self) -> None:
        """Stop any active measurement and close the serial connection."""
        if self.ppk2 is not None:
            try:
                self.ppk2.stop_measuring()
            except Exception:
                pass
            try:
                self.ppk2.toggle_DUT_power("OFF")
            except Exception:
                pass
            try:
                self.ppk2.ser.close()
            except Exception:
                pass
            self.ppk2 = None
            print("Disconnected from PPK2.")

    # ------------------------------------------------------------------
    # Measurement
    # ------------------------------------------------------------------

    def enable_dut_power(self) -> None:
        """Enable DUT power without starting current sampling."""
        if self.ppk2 is None:
            raise RuntimeError("PPK2 is not connected. Call connect() first.")
        self.ppk2.set_source_voltage(SOURCE_VOLTAGE_MV)
        self.ppk2.toggle_DUT_power("ON")

    def disable_dut_power(self) -> None:
        """Disable DUT power output."""
        if self.ppk2 is not None:
            self.ppk2.toggle_DUT_power("OFF")

    def reset_sampling_state(self) -> None:
        """Clear serial/sample parser state so the next phase starts fresh."""
        if self.ppk2 is None:
            raise RuntimeError("PPK2 is not connected. Call connect() first.")

        try:
            self.ppk2.ser.reset_input_buffer()
        except Exception:
            pass

        self.ppk2.remainder = {"sequence": b"", "len": 0}
        self.ppk2.rolling_avg = None
        self.ppk2.rolling_avg4 = None
        self.ppk2.prev_range = None
        self.ppk2.consecutive_range_samples = 0
        self.ppk2.after_spike = 0

    def start_sampling(self) -> None:
        """Begin a fresh current sampling window on the PPK2."""
        if self.ppk2 is None:
            raise RuntimeError("PPK2 is not connected. Call connect() first.")
        self.reset_sampling_state()
        self.ppk2.start_measuring()

    def stop_sampling(self) -> None:
        """Stop current sampling without changing DUT power state."""
        if self.ppk2 is not None:
            self.ppk2.stop_measuring()

    def collect_samples(self, duration_s: float) -> list[float]:
        """Stream samples from the PPK2 for the specified duration.

        Args:
            duration_s: Measurement duration in seconds.

        Returns:
            A flat list of current samples in microamperes (µA) as
            delivered by ppk2_api.get_samples().
        """
        if self.ppk2 is None:
            raise RuntimeError("PPK2 is not connected.")

        raw_buffer: list[float] = []
        deadline = time.monotonic() + duration_s
        last_progress = -1  # track last printed progress second

        print(f"Sampling for {duration_s:.0f} seconds …", end="", flush=True)

        while time.monotonic() < deadline:
            elapsed = duration_s - (deadline - time.monotonic())
            # Print a progress indicator every 5 seconds
            progress_tick = int(elapsed) // 5 * 5
            if progress_tick != last_progress and int(elapsed) > 0:
                last_progress = progress_tick
                print(f" {int(elapsed)}s", end="", flush=True)

            # get_data() returns raw bytes from the serial buffer
            raw_data = self.ppk2.get_data()
            if raw_data:
                # get_samples() converts raw bytes → (list[µA], list[digital_bits])
                samples, _ = self.ppk2.get_samples(raw_data)
                raw_buffer.extend(samples)

            time.sleep(0.01)  # avoid busy-loop; 10 ms poll interval

        print()  # newline after progress dots
        return raw_buffer

    def stop_measurement(self) -> None:
        """Stop current sampling and disable DUT power output."""
        if self.ppk2 is not None:
            try:
                self.stop_sampling()
            finally:
                self.disable_dut_power()

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    @staticmethod
    def compute_statistics(samples_ua: list[float]) -> dict:
        """Compute summary statistics from a list of µA samples.

        Args:
            samples_ua: Raw current values in microamperes (µA).

        Returns:
            dict with keys: avg_uA, min_uA, max_uA, sample_count.
        """
        if not samples_ua:
            return {"avg_uA": 0.0, "min_uA": 0.0, "max_uA": 0.0, "sample_count": 0}

        arr_ua = np.array(samples_ua, dtype=np.float64)

        return {
            "avg_uA": float(np.mean(arr_ua)),
            "min_uA": float(np.min(arr_ua)),
            "max_uA": float(np.max(arr_ua)),
            "sample_count": len(arr_ua),
        }

    # ------------------------------------------------------------------
    # CSV logging
    # ------------------------------------------------------------------

    def _ensure_csv_header(self) -> None:
        """Ensure CSV uses the current schema and migrate legacy files if needed."""
        expected_header = [
            "timestamp",
            "test_number",
            "mode",
            "avg_current_uA",
            "min_current_uA",
            "max_current_uA",
            "sample_count",
        ]
        legacy_header = [
            "timestamp",
            "test_number",
            "avg_current_uA",
            "min_current_uA",
            "max_current_uA",
            "sample_count",
        ]

        try:
            with open(self.csv_file, "x", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(expected_header)
            return
        except FileExistsError:
            pass

        with open(self.csv_file, "r", newline="") as f:
            rows = list(csv.reader(f))

        if not rows:
            with open(self.csv_file, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(expected_header)
            return

        current_header = rows[0]
        data_rows = rows[1:]

        if current_header == expected_header:
            return

        migrated_rows: list[list[str]] = []

        # Migrate known legacy schema and also normalize mixed 6/7-column rows.
        if current_header == legacy_header:
            for row in data_rows:
                if not row:
                    continue
                if len(row) == 6:
                    migrated_rows.append([row[0], row[1], "Legacy", row[2], row[3], row[4], row[5]])
                elif len(row) >= 7:
                    migrated_rows.append(row[:7])
            with open(self.csv_file, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(expected_header)
                writer.writerows(migrated_rows)
            print(f"Migrated '{self.csv_file}' to include 'mode' column.")
            return

        # Fallback: if header is unexpected but rows are 6/7 wide, normalize them.
        for row in data_rows:
            if not row:
                continue
            if len(row) == 6:
                migrated_rows.append([row[0], row[1], "Legacy", row[2], row[3], row[4], row[5]])
            elif len(row) >= 7:
                migrated_rows.append(row[:7])

        if migrated_rows:
            with open(self.csv_file, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(expected_header)
                writer.writerows(migrated_rows)
            print(f"Normalized '{self.csv_file}' to expected schema.")

    def write_result_to_csv(self, stats: dict, timestamp: str, mode: str) -> None:
        """Append one result row to the CSV file.

        Args:
            stats:     dict returned by :meth:`compute_statistics`.
            timestamp: ISO-format timestamp string for this measurement.
            mode:      Mode label (e.g., "BLE Advertisement" or "Sleep").
        """
        with open(self.csv_file, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                timestamp,
                self.test_number,
                mode,
                f"{stats['avg_uA']:.4f}",
                f"{stats['min_uA']:.4f}",
                f"{stats['max_uA']:.4f}",
                stats["sample_count"],
            ])

    @staticmethod
    def evaluate_pass_fail(ble_stats: dict, sleep_stats: dict) -> tuple[bool, str]:
        """Evaluate pass/fail based on power consumption thresholds.

        Args:
            ble_stats:   Statistics dict for BLE Advertisement mode.
            sleep_stats: Statistics dict for Sleep mode.

        Returns:
            (passed: bool, reason: str)
        """
        ble_avg = ble_stats["avg_uA"]
        sleep_avg = sleep_stats["avg_uA"]

        ble_pass = BLE_ADVERTISEMENT_MIN_UA <= ble_avg <= BLE_ADVERTISEMENT_MAX_UA
        sleep_pass = SLEEP_MODE_MIN_UA <= sleep_avg <= SLEEP_MODE_MAX_UA

        if ble_pass and sleep_pass:
            return True, "PASS"

        reasons = []
        if not ble_pass:
            reasons.append(
                f"BLE Advertisement {ble_avg:.2f} µA out of range "
                f"[{BLE_ADVERTISEMENT_MIN_UA}, {BLE_ADVERTISEMENT_MAX_UA}]"
            )
        if not sleep_pass:
            reasons.append(
                f"Sleep {sleep_avg:.2f} µA out of range "
                f"[{SLEEP_MODE_MIN_UA}, {SLEEP_MODE_MAX_UA}]"
            )

        return False, "FAIL: " + "; ".join(reasons)

    # ------------------------------------------------------------------
    # Dual-phase measurement cycle (BLE Advertisement + Sleep)
    # ------------------------------------------------------------------

    def run_measurement_cycle(self) -> None:
        """Execute a dual-phase measurement cycle:
        1. Measure BLE advertising mode for 20 seconds.
        2. Wait 10 seconds for DUT transition to sleep (DUT power remains ON).
        3. Measure sleep mode for 20 seconds using a fresh sampling window.
        Both results are logged to CSV. Pass/fail verdict is printed and logged.
        """
        self.test_number += 1
        timestamp = datetime.now().isoformat(timespec="seconds")

        print(f"\n[Test #{self.test_number}] Starting dual-phase measurement at {timestamp}")

        self.enable_dut_power()
        try:
            # --- Phase 1: BLE Advertisement Mode (20 seconds) ---
            print("\n[Phase 1] Measuring BLE Advertisement mode …")
            self.start_sampling()
            try:
                ble_samples = self.collect_samples(BLE_ADVERTISEMENT_DURATION_S)
            finally:
                self.stop_sampling()
            ble_stats = self.compute_statistics(ble_samples)

            print("\nBLE Advertisement mode complete")
            print(f"Average current: {ble_stats['avg_uA']:.2f} µA")
            print(f"Minimum current: {ble_stats['min_uA']:.2f} µA")
            print(f"Maximum current: {ble_stats['max_uA']:.2f} µA")
            print(f"Samples:         {ble_stats['sample_count']}")

            self.write_result_to_csv(ble_stats, timestamp, "BLE Advertisement")

            # --- Transition Wait (DUT power remains ON) ---
            print(f"\n[Transition] Waiting {TRANSITION_WAIT_S} seconds for DUT to transition to sleep …")
            print("(DUT power remains ON during transition)")
            time.sleep(TRANSITION_WAIT_S)

            # --- Phase 2: Sleep Mode ---
            print("\n[Phase 2] Measuring Sleep mode with a fresh 20-second sampling window …")
            self.start_sampling()
            try:
                sleep_samples = self.collect_samples(SLEEP_MODE_DURATION_S)
            finally:
                self.stop_sampling()
            sleep_stats = self.compute_statistics(sleep_samples)

            print("\nSleep mode measurement complete")
            print(f"Average current: {sleep_stats['avg_uA']:.2f} µA")
            print(f"Minimum current: {sleep_stats['min_uA']:.2f} µA")
            print(f"Maximum current: {sleep_stats['max_uA']:.2f} µA")
            print(f"Samples:         {sleep_stats['sample_count']}")

            self.write_result_to_csv(sleep_stats, timestamp, "Sleep")

            # --- Evaluate Pass/Fail ---
            passed, verdict = self.evaluate_pass_fail(ble_stats, sleep_stats)
            print(f"\n[Result] Power consumption test: {verdict}")

        finally:
            self.disable_dut_power()

        print(f"All results saved to '{self.csv_file}'.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    meter = PPK2AmpMeter(
        port=SERIAL_PORT,
        csv_file=CSV_FILE,
    )

    try:
        meter.connect()
    except ConnectionError as exc:
        print(f"ERROR: {exc}")
        print("Check that the PPK2 is connected via USB (and drivers are installed).")
        return

    print("\nPPK2 ready.")
    print(f"Press ENTER to power the DUT at {SOURCE_VOLTAGE_MV} mV and start a dual-phase measurement:")
    print(f"  • Phase 1: {BLE_ADVERTISEMENT_DURATION_S}s BLE Advertisement mode")
    print(f"  • Wait: {TRANSITION_WAIT_S}s for DUT transition to sleep")
    print(f"  • Phase 2: {SLEEP_MODE_DURATION_S}s Sleep mode")
    print(f"Press Ctrl+C to quit.\n")

    try:
        while True:
            try:
                input("Press ENTER to measure … ")
            except EOFError:
                # Non-interactive / piped input — run once and exit
                break

            try:
                meter.run_measurement_cycle()
            except serial.SerialException as exc:         # type: ignore[name-defined]
                print(f"Serial error during measurement: {exc}")
                print("Attempting to reconnect …")
                meter.disconnect()
                meter.port = SERIAL_PORT  # re-enable auto-detection if SERIAL_PORT is None
                time.sleep(2)
                try:
                    meter.connect()
                    print("Reconnected. You may try the measurement again.")
                except ConnectionError as reconn_exc:
                    print(f"Reconnect failed: {reconn_exc}")
                    break
            except Exception as exc:
                print(f"Unexpected error: {exc}")
                break

    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    finally:
        meter.disconnect()
        print("Done.")


if __name__ == "__main__":
    import serial  # imported here so the error surfaces only when actually needed
    main()
