"""Simulated car: a pretend CAN bus that answers like real modules, frame by frame.

Demo mode's other adapter ("Demo · simulated car") plugs this in where a real
CAN adapter would go, so Scan and fault-code reads/clears run the
REAL code (scan.py, TP2Transport, KWPClient, dtc.py) against it. Nothing in
that code knows it isn't a car. After each scan, check() compares what the
scan reported with what this car really has and lists any differences, so it
works as a test of the whole gateway scan.

The car is a 2011 Golf-style PQ35 with 17 modules on the gateway list, picked
to cover the awkward cases a real car can throw at the scan:
- multi-frame replies (long identities, engine with 7 codes)
- a module the gateway lists that isn't there (65 Tire Pressure)
- a listed address that isn't coded and isn't there (6C Rear View Camera)
- a module that answers but refuses every fault-code request (56 Radio)
- a module that rejects the diagnostic session but still gives codes (16)
- a module that won't identify (1A 9B refused) but gives codes (42 door)
- a Central Electronics (09) that isn't the Audi 8P BCM the app can flash:
  it has KWP 1A identity and no service 22, like a Golf's 1K0937049
- the Haldex answering on its application channel (TP2 0x764), as the
  real one does, so it isn't mistaken for a loader
Codes marked sticky come straight back after a clear (fault still present).
"""
import threading
import time

from flasher.vag_protocols.tp2 import TP2MessageReassembler, segment_message

BROADCAST = 0x200
TESTER_RX = 0x300  # the scan's tester ID (TP2Transport default)
HALDEX_APP_TX = 0x764


def _code(number, status=0x23, sticky=False):
    return {"code": number, "status": status, "sticky": sticky}


def _p(p_number, **kw):
    """Engine/gearbox code from its OBD P0 number (VAG = 16384 + number)."""
    return _code(16384 + int(p_number[1:]), **kw)


class SimModule:
    def __init__(self, address, tp2, part, version, desc, codes=(), *, coded=True, present=True,
                 identify=True, session=True, dtc=True, service22=False, vin=False):
        self.address, self.tp2, self.coded, self.present = address, tp2, coded, present
        self.part, self.version, self.desc = part, version, desc
        self.codes = [dict(c) for c in codes]
        self.identify, self.session, self.dtc, self.service22, self.vin = identify, session, dtc, service22, vin


VIN = "WVWZZZ1KZBW000123"


def default_modules():
    return [
        SimModule(0x01, 0x01, "1K0907115J", "0030", "2.0l R4/4V TFSI",
                  [_p("P0300"), _p("P0102", status=0x20, sticky=True), _p("P0299"), _p("P0171", status=0x20),
                   _p("P0420", status=0x20, sticky=True), _p("P0441"), _p("P0016", status=0x20)], vin=True),
        SimModule(0x02, 0x02, "02E927770AL", "0260", "GSG DSG AG6",
                  [_code(18258), _p("P0715", status=0x20, sticky=True)]),
        SimModule(0x03, 0x03, "1K0907379AC", "0101", "ESP MK60EC1 H31", [_code(778, 0x20, sticky=True)]),
        SimModule(0x08, 0x08, "1K0907044DT", "0120", "Climatronic"),
        SimModule(0x09, 0x20, "1K0937049N", "0560", "Bordnetz-SG H45"),  # not the 8P BCM
        SimModule(0x15, 0x15, "1K0909605AB", "0012", "AirbagVW10", [_code(532)]),
        SimModule(0x16, 0x16, "1K0953549AQ", "0060", "Lenksaeulenmodul", [_code(1528)], session=False),
        SimModule(0x17, 0x17, "1K0920874B", "0503", "KOMBI+WEGFAHRS. VDO", [_code(1314), _code(1321)]),
        SimModule(0x19, 0x1F, "1K0907530L", "0204", "J533 Gateway H", [_code(1312)], vin=True),
        SimModule(0x22, 0x0A, "0BR907554A", "6816", "Haldex 4Motion"),
        SimModule(0x42, 0x42, "1K0959701P", "0100", "Tuer-SG", [_code(1331)], identify=False),
        SimModule(0x44, 0x09, "1K0909144R", "3501", "EPS_ZFLS Kl.3"),
        SimModule(0x46, 0x46, "1K0959433CT", "0058", "KSG PQ35 RDK", [_code(1331, 0x20, sticky=True)]),
        SimModule(0x52, 0x52, "1K0959702P", "0100", "Tuer-SG"),
        SimModule(0x55, 0x55, "1K0907357A", "0013", "AFS-Steuergeraet"),
        SimModule(0x56, 0x56, "1K0035180AC", "0026", "Radio RCD510", dtc=False),
        SimModule(0x65, 0x65, "1K0907273B", "0010", "RDK", present=False),
        SimModule(0x6C, 0x6C, "", "", "", coded=False, present=False),
    ]


class SimulatedCar:
    """Duck-typed CAN device: can_send(id, data, bus) / can_recv(timeout_ms) / close()."""

    def __init__(self, modules=None, log=None):
        self.modules = modules or default_modules()
        self.by_tp2 = {m.tp2: m for m in self.modules if m.present}
        self.log = log or (lambda message: None)
        self.lock = threading.Lock()
        self.outbox = []
        self.channel = None  # (module, tester->ecu id, ecu->tester id)
        self.rx = None       # reassembler for the open channel
        self.tx_seq = 0
        self.requests = []   # (tp2, request hex) for the self-check log

    # --- CAN device API ---

    def close(self):
        pass  # kept between jobs, so clears stick like on a car

    def can_recv(self, timeout_ms):
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            with self.lock:
                if self.outbox:
                    out, self.outbox = self.outbox, []
                    return out
            if time.monotonic() >= deadline:
                return []
            time.sleep(0.001)

    def can_send(self, can_id, data, bus=0):
        data = bytes(data)
        with self.lock:
            if can_id == BROADCAST:
                self._setup(data, bus)
            elif self.channel and can_id == self.channel[1]:
                self._channel_frame(data, bus)

    # --- TP2.0 ---

    def _reply(self, can_id, data, bus):
        self.outbox.append((can_id, bytes(data), bus))

    def _setup(self, data, bus):
        if len(data) != 7 or data[1] != 0xC0:
            return
        module = self.by_tp2.get(data[0])
        if module is None:
            return  # nothing at this ID: the tester times out, as on a car
        to_ecu = HALDEX_APP_TX if module.tp2 == 0x0A else 0x740 + (module.tp2 & 0x1F)
        tester = data[4] | (data[5] << 8)
        self.channel = (module, to_ecu, tester)
        self.rx = TP2MessageReassembler()
        self.tx_seq = 0
        self._reply(BROADCAST + module.tp2,
                    bytes([0x00, 0xD0, tester & 0xFF, tester >> 8, to_ecu & 0xFF, to_ecu >> 8, 0x01]), bus)

    def _channel_frame(self, data, bus):
        module, _to_ecu, to_tester = self.channel
        if data[0] in (0xA0, 0xA3):
            self._reply(to_tester, b"\xA1\x0F\x8A\xFF\x32\xFF", bus)
            return
        if data[0] == 0xA8:
            self.channel = None
            return
        if data[0] >> 4 == 0xB or data[0] >> 4 == 0x9:
            return  # the tester acknowledging our reply
        if data[0] >> 4 not in (0, 1, 2, 3):
            return
        message, ack = self.rx.feed(data)
        if ack is not None:
            self._reply(to_tester, ack, bus)
        if message is None:
            return
        self.requests.append((module.tp2, message.hex()))
        response = self._kwp(module, message)
        frames, self.tx_seq = segment_message(response, self.tx_seq, block_size=0)
        for frame in frames:
            self._reply(to_tester, frame, bus)

    # --- KWP2000 ---

    @staticmethod
    def _nrc(sid, code=0x11):
        return bytes([0x7F, sid, code])

    def _kwp(self, m, request):
        sid = request[0]
        if sid == 0x10:
            return bytes([0x50, request[1]]) if m.session else self._nrc(0x10, 0x12)
        if sid == 0x1A and len(request) == 2:
            which = request[1]
            if which == 0x9B and m.identify:
                return (b"\x5A\x9B" + m.part.encode().ljust(12) + m.version.encode().ljust(4)
                        + b"\x00" * 10 + m.desc.encode())
            if which == 0x9C and m.identify:
                return b"\x5A\x9C\x00\x00\x00\x00\x10\x06\x15\x00\x00"
            if which == 0x90 and m.vin:
                return b"\x5A\x90" + VIN.encode()
            if which == 0x9F and m.tp2 == 0x1F:
                body = bytearray(b"\x5A\x9F\x01")
                for other in self.modules:
                    body += bytes([other.address, other.tp2, 0x00, 0x01 if other.coded else 0x00])
                return bytes(body + b"\xFF")
            return self._nrc(0x1A, 0x12 if which == 0x9F else 0x11)
        if sid == 0x22:
            return self._nrc(0x22, 0x11)  # no service 22 (the 8P BCM would answer here)
        if sid in (0x18, 0x13, 0x03):
            if not m.dtc:
                return self._nrc(sid, 0x11)
            if sid == 0x18:
                body = bytearray([0x58, len(m.codes)])
                for c in m.codes:
                    body += bytes([c["code"] >> 8, c["code"] & 0xFF, c["status"]])
                return bytes(body)
            return self._nrc(sid, 0x11)
        if sid == 0x12 and len(request) == 5 and request[1:3] == b"\x00\x04":
            # Freeze frame (environment data) for one code: priority, frequency, reset counter,
            # mileage (3 bytes, km), speed, as the flasher's decode_freeze_frame reads them.
            number = (request[3] << 8) | request[4]
            code = next((c for c in m.codes if c["code"] == number), None)
            if not m.dtc or code is None:
                return self._nrc(0x12, 0x31)
            km = 148204 - 7 * (number % 50)
            return (b"\x52" + request[3:5] + bytes([0x6C, code.get("priority", 2), 0x2B, 1, 0x02, 13,
                                                   0x03, km >> 16 & 0xFF, km >> 8 & 0xFF, km & 0xFF,
                                                   0x58, 0]))
        if sid == 0x14:
            if not m.dtc:
                return self._nrc(0x14, 0x11)
            m.codes = [c for c in m.codes if c["sticky"]]
            return b"\x54\xFF\x00"
        return self._nrc(sid, 0x11)

    # --- self-check ---

    def check(self, results, gateway):
        """Compare a scan's results with this car. {"passed", "total", "problems": [text]}."""
        problems, total = [], 0

        def expect(ok, text):
            nonlocal total
            total += 1
            if not ok:
                problems.append(text)

        listed = gateway.get("entries") or []
        expect(gateway.get("available") and len(listed) == len(self.modules),
               f"Gateway list: expected {len(self.modules)} entries, got {len(listed)}")
        expect((gateway.get("vehicle") or {}).get("vin") == VIN,
               f"VIN: expected {VIN}, got {(gateway.get('vehicle') or {}).get('vin')}")
        quick = {e["family"]: e for e in results}
        for family, want in (("engine", "ok"), ("transmission", "ok"), ("haldex-gen4", "ok"),
                             ("pq-eps", "ok"), ("pq-bcm", "unexpected")):
            got = (quick.get(family) or {}).get("status")
            expect(got == want, f"Quick scan {family}: expected {want}, got {got}")
        got = {m["tp2"]: m for m in gateway.get("modules") or []}
        for m in self.modules:
            name = f"{m.address:02X}"
            row = got.get(m.tp2)
            if row is None:
                expect(False, f"{name}: missing from the full scan")
                continue
            if not m.present:
                expect(row["status"] == "no_answer", f"{name}: expected no answer, got {row['status']}")
                expect(row.get("coded") == m.coded, f"{name}: coded flag {row.get('coded')}, expected {m.coded}")
                continue
            expect(row["status"] == "ok", f"{name}: expected to answer, got {row['status']}")
            ident = row.get("identity") or {}
            if m.identify:
                expect(ident.get("software_part_number") == m.part,
                       f"{name}: part {ident.get('software_part_number')!r}, expected {m.part!r}")
            report = row.get("dtc") or {}
            if not m.dtc:
                expect(bool(report.get("error")), f"{name}: should report that it gives no fault codes")
                continue
            want = sorted(c["code"] for c in m.codes)
            have = sorted(c["code"] for c in report.get("codes") or [])
            expect(not report.get("error") and want == have,
                   f"{name}: codes {have or 'none'}{' (' + str(report.get('error')) + ')' if report.get('error') else ''}, "
                   f"expected {want or 'none'}")
        return {"passed": total - len(problems), "total": total, "problems": problems}


def adapter_entry():
    return {"adapter": "simcar", "label": "Demo · simulated car (tests the real scan)", "present": True}
