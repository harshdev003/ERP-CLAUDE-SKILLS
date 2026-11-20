"""Pins what `eng:reclaim-disk` offers from the simulator side of a Mac.

On 2026-10-02 the tool reported "Nothing reclaimable found" on a Mac where a hand
sweep then freed ~190 GB, most of it simulator runtimes nobody used and
simulators whose worktree was gone. The selection rules below are what decide
those, and each one errs toward keeping — so the tests check both directions:
the runtime nothing uses IS offered, and the one a device or Xcode needs is NOT.

Fixtures are in the exact shape `xcrun simctl runtime list -j` and
`xcrun simctl list devices -j` emit (captured from Xcode 27.0), trimmed to the
fields the script reads. Every subprocess goes through `R.run` / `R.run_checked`,
which the tests replace — no simctl, no macOS needed.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

SCRIPT_DIR = (pathlib.Path(__file__).resolve().parents[1]
              / "plugins/eng/skills/reclaim-disk/scripts")
sys.path.insert(0, str(SCRIPT_DIR))

import reclaim_disk as R  # noqa: E402

DAY = 86400
NOW = time.time()


def stamp(days_ago: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(NOW - days_ago * DAY))


def runtime(rid, plat, version, build, last_used=None, size=8_000_000_000):
    r = {"build": build, "deletable": True, "identifier": rid,
         "platformIdentifier": f"com.apple.platform.{plat}",
         "runtimeIdentifier": f"com.apple.CoreSimulator.SimRuntime.{plat}-{version.replace('.', '-')}",
         "runtimeBundlePath": f"/Library/Developer/CoreSimulator/Volumes/{build}/{version}.simruntime",
         "sizeBytes": size, "state": "Ready", "version": version}
    if last_used is not None:
        r["lastUsedAt"] = stamp(last_used)
    return r


DEVICE_TYPES = {  # `simctl list devicetypes -j`, the entries the fixtures use
    "com.apple.CoreSimulator.SimDeviceType.iPhone-17-Pro": "iPhone 17 Pro",
    "com.apple.CoreSimulator.SimDeviceType.iPhone-SE-3rd-generation": "iPhone SE (3rd generation)",
    "com.apple.CoreSimulator.SimDeviceType.iPad-Pro-13-inch-M4-8GB": "iPad Pro 13-inch (M4)",
    "com.apple.CoreSimulator.SimDeviceType.Apple-Watch-Series-11-46mm": "Apple Watch Series 11 (46mm)",
}
TYPE_BY_NAME = {v: k for k, v in DEVICE_TYPES.items()}


def device(udid, name, state="Shutdown", last_used=1.0, type_name=None):
    if type_name is None:
        type_name = max((n for n in TYPE_BY_NAME if name.startswith(n)), key=len, default="")
    return {"udid": udid, "name": name, "state": state, "isAvailable": True,
            "lastUsedAt": stamp(last_used),
            "deviceTypeIdentifier": TYPE_BY_NAME.get(type_name, "unknown"),
            "dataPath": f"/nonexistent/{udid}/data"}


class FakeSimctl:
    """Answers the three simctl reads the scanners make; everything else is empty."""

    def __init__(self, runtimes=None, devices=None):
        self.runtimes = runtimes or {}
        self.devices = devices or {}

    def __call__(self, *args, timeout=120):
        if args[:4] == ("xcrun", "simctl", "runtime", "list"):
            return json.dumps(self.runtimes)
        if args[:4] == ("xcrun", "simctl", "list", "devices"):
            return json.dumps({"devices": self.devices})
        if args[:4] == ("xcrun", "simctl", "list", "devicetypes"):
            return json.dumps({"devicetypes": [{"identifier": i, "name": n}
                                               for i, n in DEVICE_TYPES.items()]})
        return ""


IOS_265 = "com.apple.CoreSimulator.SimRuntime.iphonesimulator-26-5"
IOS_262 = "com.apple.CoreSimulator.SimRuntime.iphonesimulator-26-2"


class TestSimRuntimes(unittest.TestCase):
    def offered(self, runtimes, devices=None, days=30):
        with mock.patch.object(R, "run", FakeSimctl(runtimes, devices)):
            return {c.remove.split(":", 1)[1] for c in R.scan_sim_runtimes(days=days)}

    def test_the_2026_10_02_machine(self):
        """Two iOS runtimes in use, three old iOS ones, a never-used watchOS and visionOS."""
        runtimes = {
            "A": runtime("A", "iphonesimulator", "26.5", "23F77", last_used=0.1),   # newest iOS
            "B": runtime("B", "iphonesimulator", "26.2", "23C54", last_used=0.2),   # has devices
            "C": runtime("C", "iphonesimulator", "26.4", "23E244", last_used=128),
            "D": runtime("D", "iphonesimulator", "18.5", "22F77", last_used=8),     # used last week
            "E": runtime("E", "iphonesimulator", "17.2", "21C62", last_used=200),
            "W": runtime("W", "watchsimulator", "26.4", "23T239"),                  # never used
            "V": runtime("V", "xrsimulator", "26.0", "23M336"),                     # never used
        }
        devices = {IOS_262: [device("U1", "iPhone 17 Pro (fix)")], IOS_265: []}
        self.assertEqual(self.offered(runtimes, devices), {"C", "E", "W", "V"})

    def test_a_runtime_with_a_simulator_on_it_is_kept_however_old(self):
        runtimes = {"A": runtime("A", "iphonesimulator", "26.5", "23F77", last_used=0),
                    "B": runtime("B", "iphonesimulator", "26.2", "23C54", last_used=400)}
        self.assertEqual(self.offered(runtimes, {IOS_262: [device("U1", "iPhone 17 Pro")]}), set())

    def test_the_newest_runtime_of_a_used_platform_is_kept_even_when_stale(self):
        runtimes = {"A": runtime("A", "iphonesimulator", "26.5", "23F77", last_used=90),
                    "B": runtime("B", "iphonesimulator", "26.2", "23C54", last_used=90)}
        self.assertEqual(self.offered(runtimes), {"B"})

    def test_newest_is_by_version_not_by_string(self):
        runtimes = {"A": runtime("A", "iphonesimulator", "26.10", "X", last_used=90),
                    "B": runtime("B", "iphonesimulator", "26.9", "Y", last_used=90)}
        self.assertEqual(self.offered(runtimes), {"B"})

    def test_a_platform_never_used_keeps_nothing(self):
        runtimes = {"W1": runtime("W1", "watchsimulator", "26.4", "a"),
                    "W2": runtime("W2", "watchsimulator", "11.5", "b")}
        self.assertEqual(self.offered(runtimes), {"W1", "W2"})

    def test_recently_used_is_kept_and_the_window_is_the_flag(self):
        runtimes = {"A": runtime("A", "iphonesimulator", "26.5", "23F77", last_used=0),
                    "D": runtime("D", "iphonesimulator", "18.5", "22F77", last_used=8)}
        self.assertEqual(self.offered(runtimes, days=30), set())
        self.assertEqual(self.offered(runtimes, days=7), {"D"})

    def test_a_runtime_mid_delete_or_not_deletable_is_not_offered(self):
        busy = runtime("C", "iphonesimulator", "26.4", "c", last_used=200)
        busy["state"] = "Deleting"
        locked = runtime("E", "iphonesimulator", "17.2", "e", last_used=200)
        locked["deletable"] = False
        newest = runtime("A", "iphonesimulator", "26.5", "a", last_used=0)
        self.assertEqual(self.offered({"A": newest, "C": busy, "E": locked}), set())

    def test_offered_runtimes_are_a_policy_hold_sized_from_simctl(self):
        runtimes = {"A": runtime("A", "iphonesimulator", "26.5", "a", last_used=0),
                    "C": runtime("C", "iphonesimulator", "26.4", "c", last_used=200, size=8_500_000_000)}
        with mock.patch.object(R, "run", FakeSimctl(runtimes)):
            (c,) = R.scan_sim_runtimes()
        self.assertEqual(c.flag, "!")
        self.assertFalse(c.takeable(set()))
        self.assertTrue(c.takeable({"sim-runtimes"}))
        self.assertEqual(c.kb, 8_500_000_000 // 1024)

    def test_unparseable_simctl_output_offers_nothing(self):
        with mock.patch.object(R, "run", lambda *a, **k: "xcrun: error"):
            self.assertEqual(R.scan_sim_runtimes(), [])


class TestOrphanSimulators(unittest.TestCase):
    def offered(self, devices, worktrees):
        with mock.patch.object(R, "run", FakeSimctl(devices=devices)), \
             mock.patch.object(R, "worktrees_of", lambda repo: worktrees):
            return {c.why.split(" · ")[0]: c for c in R.scan_orphan_simulators(["/repo"])}

    def test_only_labels_no_scanned_worktree_owns_are_offered(self):
        devices = {IOS_262: [
            device("1", "iPhone 17 Pro (prod-6328-design)"),   # its worktree exists
            device("2", "iPhone 17 Pro (fix-dark)"),           # worktree `fix` + suffix
            device("3", "iPhone 17 Pro (main)"),               # the primary checkout
            device("4", "iPhone 17 Pro (ci)"),
            device("5", "iPhone 17 Pro"),                      # not a session device
            device("6", "iPhone 17 Pro (serene-bose-3bfd24)"), # worktree removed
        ]}
        trees = ["/repo", "/repo/.claude/worktrees/prod-6328-design", "/repo/.claude/worktrees/fix"]
        self.assertEqual(set(self.offered(devices, trees)), {"iPhone 17 Pro (serene-bose-3bfd24)"})

    def test_stock_devices_whose_type_name_has_parentheses_are_never_offered(self):
        devices = {IOS_262: [
            device("1", "iPad Pro 13-inch (M4)"),
            device("2", "Apple Watch Series 11 (46mm)"),
            device("3", "iPhone SE (3rd generation)"),
            device("4", "iPhone SE (3rd generation) (gone)"),   # a session device on that type
        ]}
        self.assertEqual(set(self.offered(devices, ["/repo"])), {"iPhone SE (3rd generation) (gone)"})

    def test_a_device_whose_type_cannot_be_resolved_is_kept(self):
        devices = {IOS_262: [device("1", "iPhone 17 Pro (gone)")]}
        with mock.patch.object(R, "device_type_names", lambda: {}):
            self.assertEqual(self.offered(devices, ["/repo"]), {})

    def test_a_worktree_name_is_matched_the_way_the_harness_sanitises_it(self):
        devices = {IOS_262: [device("1", "iPhone 17 Pro (my-fix)")]}
        self.assertEqual(self.offered(devices, ["/repo", "/repo/.claude/worktrees/my fix"]), {})

    def test_an_orphan_is_a_policy_hold_and_a_running_one_is_in_use(self):
        devices = {IOS_262: [device("1", "iPhone 17 Pro (gone)"),
                             device("2", "iPhone 17 Pro (gone-too)", state="Booted")]}
        rows = self.offered(devices, ["/repo"])
        self.assertEqual(rows["iPhone 17 Pro (gone)"].flag, "!")
        self.assertEqual(rows["iPhone 17 Pro (gone-too)"].flag, "x")
        self.assertFalse(rows["iPhone 17 Pro (gone-too)"].takeable({"orphan-simulators"}))

    def test_unavailable_runtimes_are_left_to_the_simulators_source(self):
        devices = {"com.apple.CoreSimulator.SimRuntime.iOS-17-2 (unavailable, runtime missing)":
                   [device("1", "iPhone 15 (gone)")]}
        self.assertEqual(self.offered(devices, ["/repo"]), {})

    def test_the_source_needs_a_repo(self):
        args = argparse.Namespace(only=["orphan-simulators"], repo=[], include_built=False,
                                  days=7, runtime_days=30)
        with mock.patch.object(R, "live_build_roots", lambda: []), \
             mock.patch.object(R, "scan_orphan_simulators", side_effect=AssertionError("ran")):
            self.assertEqual(R.collect(args), [])


class TestSimAttachments(unittest.TestCase):
    def test_only_a_shut_down_device_with_content_is_offered(self):
        with tempfile.TemporaryDirectory() as tmp:
            def make(udid, content):
                att = os.path.join(tmp, udid, "data/Containers/Data/InternalDaemon/X/Attachments")
                os.makedirs(att)
                if content:
                    open(os.path.join(att, "rec.mov"), "w").close()
                return {**device(udid, f"iPhone ({udid})"), "dataPath": os.path.join(tmp, udid, "data")}
            devs = [make("idle", True), make("empty", False), {**make("live", True), "state": "Booted"}]
            with mock.patch.object(R, "run", FakeSimctl(devices={IOS_262: devs})), \
                 mock.patch.object(R, "du_kb", lambda p: 2 * 1024 * 1024):
                rows = {c.why.split(" · ")[0]: c.flag for c in R.scan_sim_attachments()}
        self.assertEqual(rows, {"iPhone (idle)": "!", "iPhone (live)": "x"})

    def test_reclaim_empties_the_folder_and_keeps_it(self):
        with tempfile.TemporaryDirectory() as att:
            os.makedirs(os.path.join(att, "sub"))
            open(os.path.join(att, "rec.mov"), "w").close()
            c = R.Candidate("sim-attachments", att, 1, "x", remove="empty", safe=False)
            args = argparse.Namespace(only=["sim-attachments"], include_built=False, confirm=True)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(R.reclaim([c], args), 0)
            self.assertTrue(os.path.isdir(att))
            self.assertEqual(os.listdir(att), [])


class TestReportedOnly(unittest.TestCase):
    def test_a_manual_row_is_never_takeable_whatever_the_flags(self):
        c = R.Candidate("dev-caches", "/x", 10, "npm", remove="manual", safe=False)
        self.assertEqual(c.flag, "-")
        self.assertFalse(c.takeable({"dev-caches"}, {"include-built"}))

    def test_report_separates_the_four_kinds_and_never_says_nothing_when_rows_exist(self):
        rows = [R.Candidate("dev-caches", "/a", 1024 * 1024, "npm", remove="manual", safe=False),
                R.Candidate("sim-runtimes", "/b", 2048, "old", safe=False),
                R.Candidate("derived-data", "/c", 4096, "gone")]
        out = io.StringIO()
        with contextlib.redirect_stdout(out), mock.patch.object(R, "free_kb", lambda *a: 0):
            R.report(rows, argparse.Namespace(json=False, top=5))
        text = out.getvalue()
        self.assertNotIn("Nothing reclaimable", text)
        self.assertIn("Reclaimable now: 4.0 MB", text)
        self.assertIn("held for a decision: 2.0 MB (!)", text)
        self.assertIn("reported only: 1.0 GB (-)", text)


class TestRuntimeDeleteFailure(unittest.TestCase):
    """A failed `simctl runtime delete` must reach the exit code, not count as reclaimed."""

    def test_a_failed_delete_exits_1(self):
        c = R.Candidate("sim-runtimes", "/r", 1, "old", remove="runtime:ABC", safe=False)
        calls = []

        def fail(*args, timeout=120):
            calls.append(args)
            return False, "CoreSimulator is not responding"

        args = argparse.Namespace(only=["sim-runtimes"], include_built=False, confirm=True)
        with mock.patch.object(R, "run_checked", fail), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(R.reclaim([c], args), 1)
        self.assertEqual(calls, [("xcrun", "simctl", "runtime", "delete", "ABC")])


class TestNvmrcPins(unittest.TestCase):
    def test_pins_are_read_with_or_without_v_and_hidden_dirs_are_skipped(self):
        with tempfile.TemporaryDirectory() as home:
            for rel, pin in [("Dev/Web/UserWeb", "24.18.0"), ("Dev/Legacy/Fns", "v10.15.0"),
                             (".hidden/x", "12.0.0"), ("a/b/c/d/e/deep", "8.0.0")]:
                os.makedirs(os.path.join(home, rel))
                with open(os.path.join(home, rel, ".nvmrc"), "w") as fh:
                    fh.write(pin + "\n")
            self.assertEqual(R.nvmrc_pins(home), {"24.18.0", "10.15.0"})


if __name__ == "__main__":
    unittest.main()
