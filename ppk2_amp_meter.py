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
    3. Run:  python ppk2_amp_meter.py
     4. Press ENTER to start each 30-second measurement cycle.
         The script enables DUT power output at 3600 mV when the cycle starts.
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
MEASUREMENT_DURATION_S = 30    # Duration of each measurement cycle (seconds)
CSV_FILE = "results.csv"       # Output CSV file (created if it does not exist)
# ---------------------------------------------------------------------------


class PPK2AmpMeter:
    """Wrap the PPK2 API for repeated Source Meter measurements.

    The class name is kept for compatibility with the original requirement,
    but the measurement flow now uses *Source Meter* mode. The PPK2 powers the
    DUT at a fixed voltage and measures the DUT current during each cycle.
    """

    def __init__(self, port: str | None = None, duration_s: float = 30.0, csv_file: str = CSV_FILE):
        self.port = port  # None triggers auto-detection in connect()
        self.duration_s = duration_s
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

    def start_measurement(self) -> None:
        """Enable DUT power and begin current sampling on the PPK2."""
        if self.ppk2 is None:
            raise RuntimeError("PPK2 is not connected. Call connect() first.")
        self.ppk2.set_source_voltage(SOURCE_VOLTAGE_MV)
        self.ppk2.toggle_DUT_power("ON")
        self.ppk2.start_measuring()

    def collect_samples(self) -> list[float]:
        """Stream samples from the PPK2 for `self.duration_s` seconds.

        Returns a flat list of current samples in microamperes (µA) as
        delivered by ppk2_api.get_samples().
        """
        if self.ppk2 is None:
            raise RuntimeError("PPK2 is not connected.")

        raw_buffer: list[float] = []
        deadline = time.monotonic() + self.duration_s
        last_progress = -1  # track last printed progress second

        print(f"Sampling for {self.duration_s:.0f} seconds …", end="", flush=True)

        while time.monotonic() < deadline:
            elapsed = self.duration_s - (deadline - time.monotonic())
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
                self.ppk2.stop_measuring()
            finally:
                self.ppk2.toggle_DUT_power("OFF")

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
        """Write the CSV header row if the file does not already exist."""
        try:
            with open(self.csv_file, "x", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(
                    ["timestamp", "test_number", "avg_current_uA",
                     "min_current_uA", "max_current_uA", "sample_count"]
                )
        except FileExistsError:
            pass

    def write_result_to_csv(self, stats: dict, timestamp: str) -> None:
        """Append one result row to the CSV file.

        Args:
            stats:     dict returned by :meth:`compute_statistics`.
            timestamp: ISO-format timestamp string for this measurement.
        """
        with open(self.csv_file, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                timestamp,
                self.test_number,
                f"{stats['avg_uA']:.4f}",
                f"{stats['min_uA']:.4f}",
                f"{stats['max_uA']:.4f}",
                stats["sample_count"],
            ])

    # ------------------------------------------------------------------
    # Single measurement cycle (high-level)
    # ------------------------------------------------------------------

    def run_measurement_cycle(self) -> None:
        """Execute one complete 30-second measurement cycle and log the result."""
        self.test_number += 1
        timestamp = datetime.now().isoformat(timespec="seconds")

        print(f"\n[Test #{self.test_number}] Starting measurement at {timestamp}")

        self.start_measurement()
        try:
            samples = self.collect_samples()
        finally:
            # Always stop the PPK2 even if an exception is raised mid-flight
            self.stop_measurement()

        stats = self.compute_statistics(samples)

        # --- Console output ---
        print("\nMeasurement complete")
        print(f"Average current: {stats['avg_uA']:.2f} µA")
        print(f"Minimum current: {stats['min_uA']:.2f} µA")
        print(f"Maximum current: {stats['max_uA']:.2f} µA")
        print(f"Samples:         {stats['sample_count']}")

        # --- CSV logging ---
        self.write_result_to_csv(stats, timestamp)
        print(f"Result saved to '{self.csv_file}'.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    meter = PPK2AmpMeter(
        port=SERIAL_PORT,
        duration_s=MEASUREMENT_DURATION_S,
        csv_file=CSV_FILE,
    )

    try:
        meter.connect()
    except ConnectionError as exc:
        print(f"ERROR: {exc}")
        print("Check that the PPK2 is connected via USB (and drivers are installed).")
        return

    print("\nPPK2 ready.")
    print(f"Press ENTER to power the DUT at {SOURCE_VOLTAGE_MV} mV and start a measurement, or Ctrl+C to quit.\n")

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
