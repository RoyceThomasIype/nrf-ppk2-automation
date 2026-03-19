# SIReader PCB Power Consumption Test Procedure

This document describes the full procedure to measure power consumption for each SIReader PCB using a Nordic Power Profiler Kit II (PPK2) and the automation script in this repository.

## 1. Purpose

Use this procedure to:

- Power each SIReader PCB from the PPK2 in Source Meter mode.
- Capture current samples for exactly 30 seconds per run.
- Log repeatable results to CSV.
- Compare boards and detect outliers.

## 2. Test Setup

### 2.1 Required Hardware

- Nordic Power Profiler Kit II (PPK2)
- SIReader PCB under test (one board at a time)
- USB cable for PPK2
- DUT wiring leads
- PC with Python installed

### 2.2 Required Software

- Python 3.10+ recommended
- Python dependencies listed in requirements.txt

Install dependencies:

```bash
pip install -r requirements.txt
```

Alternative (manual install):

```bash
pip install ppk2-api pyserial numpy
```

## 3. Repository Files

- requirements.txt: Python dependencies
- ppk2_amp_meter.py: Automation script for measurement and CSV logging
- results.csv: Output file containing measurement summaries

## 4. Electrical Connection

Connect the PPK2 to one SIReader PCB at a time.

- PPK2 is used in Source Meter mode.
- DUT supply is set to 3600 mV by the script.
- The script turns DUT power ON when measurement starts and OFF when measurement stops.

Before running measurements:

- Verify polarity and wiring.
- Verify there are no shorts.
- Ensure the board is in the intended operating state for measurement.

## 5. Port Selection Behavior

The script supports automatic PPK2 port detection.

- Default configuration uses SERIAL_PORT = None.
- The script scans Nordic USB CDC ACM ports and selects the best candidate.
- If required, you can force a specific port by editing SERIAL_PORT in ppk2_amp_meter.py (example: COM7).

## 6. Configure Test Parameters

Open ppk2_amp_meter.py and confirm these constants:

- SERIAL_PORT = None
- SOURCE_VOLTAGE_MV = 3600
- MEASUREMENT_DURATION_S = 30
- CSV_FILE = "results.csv"

## 7. Running the Test

Run from the project folder:

```bash
python ppk2_amp_meter.py
```

Expected startup flow:

1. PPK2 is detected and connected.
2. Source Meter mode is configured.
3. Prompt is shown: press Enter to start a measurement.

## 8. Standard Procedure Per SIReader PCB

Use this exact sequence for consistency:

1. Connect one SIReader PCB to the PPK2 test wiring.
2. Run the script.
3. Wait for the "PPK2 ready" prompt.
4. Press Enter to start measurement.
5. Script powers DUT at 3600 mV and samples for 30 seconds.
6. Script prints summary values in uA:
   - Average current
   - Minimum current
   - Maximum current
   - Sample count
7. Script appends one row to results.csv.
8. Label/save the result against the PCB identifier in your production log.
9. Disconnect the PCB and connect the next one.
10. Press Enter again for the next board.

Notes:

- Test number auto-increments for each cycle in one script session.
- Press Ctrl+C to stop testing safely.
- On exit, measurement is stopped and DUT power is turned OFF.

## 9. CSV Output Format

results.csv columns:

- timestamp
- test_number
- avg_current_uA
- min_current_uA
- max_current_uA
- sample_count

Example row:

```csv
2026-03-19T10:12:33,5,2415.8831,1780.4420,3299.1927,120000
```

## 10. Production Logging Recommendation

results.csv only stores test_number, not PCB serial number. For production traceability, keep a parallel log (spreadsheet or database) with:

- PCB serial number
- Operator
- Date/time
- Corresponding test_number from console/CSV
- Pass/fail decision
- Notes (firmware version, test mode, anomalies)

## 11. Troubleshooting

### PPK2 not found

Symptoms:

- "No PPK2 device found"
- Serial open failure

Actions:

1. Reconnect USB cable.
2. Check Device Manager for PPK2 COM ports.
3. Close other tools that may hold the COM port.
4. Retry script.

### Two COM ports visible for PPK2

This is normal on Windows. The script auto-selects the preferred Nordic CDC ACM port.

### Serial error during measurement

The script attempts to reconnect automatically. If reconnect fails:

1. Stop script.
2. Reconnect hardware.
3. Start script again.

### Unexpectedly high or unstable current

1. Recheck DUT wiring and polarity.
2. Ensure test firmware and operating state are correct.
3. Repeat measurement for the same board.
4. Compare against known-good board baseline.

## 12. End-of-Test Checklist

Before finishing a batch:

1. Confirm all boards were tested.
2. Confirm results.csv has expected number of new rows.
3. Confirm each row is mapped to a PCB identifier in your production log.
4. Archive results with date and batch information.

---

Maintainer note: If test requirements change (voltage, duration, CSV schema), update both ppk2_amp_meter.py and this README so operators always follow current behavior.
