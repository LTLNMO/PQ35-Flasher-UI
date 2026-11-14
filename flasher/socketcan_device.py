#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
socketcan_device.py
python-can adapter implementing the device interface required by TP20Transport and HaldexFlasher.
Supports Linux SocketCAN (can0) and gs_usb/candleLight USB adapters (Windows or Linux).
"""
import time
import logging
from typing import List, Tuple, Optional

logger = logging.getLogger("SocketCANDevice")


def _import_can():
    # Imported on first use so offline imports do not load adapter drivers.
    try:
        import can
    except ImportError:
        return None
    return can


def default_channel(interface: str):
    return 0 if interface == "gs_usb" else "can0"


# gs_usb / candleLight USB IDs (kept in step with pq35ui.worker.discovery).
_GS_USB_IDS = {(0x1D50, 0x606F), (0x1209, 0x2323), (0x1CD2, 0x606F), (0x16D0, 0x10B8)}


def _reset_gs_usb(index):
    """USB-reset the Nth gs_usb device so a stale claim from a previous operation
    clears. macOS can leave the device claimed after one op, so the next open
    fails with "No such device" / "Access denied" until it is reset."""
    try:
        import usb.core

        def vid_pid(d):
            try:
                return (d.idVendor, d.idProduct)
            except Exception:
                return (None, None)
        devices = [d for d in usb.core.find(find_all=True) if vid_pid(d) in _GS_USB_IDS]
        if 0 <= index < len(devices):
            devices[index].reset()
    except Exception:
        pass


def close_can_bus(bus):
    """Close a python-can bus so it can be reopened later.

    For gs_usb (candleLight) on macOS, python-can's own ``bus.shutdown()`` runs a
    re-scan + start/stop workaround that leaves the device claimed and unopenable
    until it is physically replugged. Release the USB interface directly instead,
    and mark the bus shut so python-can doesn't run that path afterwards."""
    if bus is None:
        return
    if type(bus).__name__ == "GsUsbBus" and getattr(bus, "gs_usb", None) is not None:
        import usb.util
        gs = bus.gs_usb
        device = getattr(gs, "gs_usb", None)
        try:
            gs.stop()
        except Exception:
            pass
        if device is not None:
            try:
                usb.util.release_interface(device, 0)
            except Exception:
                pass
            try:
                usb.util.dispose_resources(device)
            except Exception:
                pass
        try:
            bus._is_shutdown = True
        except Exception:
            pass
        return
    try:
        bus.shutdown()
    except Exception:
        pass


def open_can_bus(interface: str = "socketcan", channel=None, bitrate: int = 500000):
    """Open a python-can bus. SocketCAN takes an interface name and is configured
    by the OS; gs_usb takes a USB device index and is configured here at bitrate."""
    can = _import_can()
    if can is None:
        raise RuntimeError("python-can is not installed. Please install with 'pip install python-can'.")
    if channel is None:
        channel = default_channel(interface)
    if interface == "socketcan":
        return can.Bus(interface="socketcan", channel=channel, receive_own_messages=False)
    if interface == "gs_usb":
        try:
            index = int(channel)
        except (TypeError, ValueError):
            raise ValueError(f"gs_usb channel must be a USB device index (0, 1, ...), not {channel!r}")
        try:
            # Windows has no system libusb; load the bundled DLL before gs_usb asks
            # pyusb for its (cached) default backend.
            import libusb_package
            from usb.backend import libusb1
            libusb1.get_backend(find_library=libusb_package.find_library)
        except ImportError:
            pass
        # A freshly plugged device can take a moment to enumerate; retry briefly.
        # (Reuse of an already-open bus is handled by the worker, since gs_usb
        # cannot be reliably closed and reopened on macOS.)
        import time
        last_error = None
        for attempt in range(3):
            try:
                return can.Bus(interface="gs_usb", channel=index, index=index, bitrate=bitrate)
            except Exception as exc:
                last_error = exc
                time.sleep(0.5)
        raise last_error
    raise ValueError(f"Unsupported python-can interface: {interface!r}")


class SocketCANDevice:
    """
    Duck-typed CAN device interface matching the methods expected by TP20Transport:
      - can_send(addr, dat, bus=0)
      - can_recv(timeout_ms=20) -> List[Tuple[addr, dat, bus]]
      - can_clear(mask=0xFFFF)
      - write_frame(addr, dat)
      - close()
    """
    def __init__(self, channel=None, bitrate: int = 500000, bus: Optional[object] = None,
                 interface: str = "socketcan"):
        self.interface = interface
        self.channel = default_channel(interface) if channel is None else channel
        self.bitrate = bitrate
        self.bus_num = 0
        self._can = _import_can()
        if bus is not None:
            self.bus = bus
        else:
            try:
                self.bus = open_can_bus(interface, self.channel, bitrate)
                logger.info(f"{interface} device opened on channel {self.channel}")
            except Exception as e:
                logger.error(f"Failed to open {interface} on {self.channel}: {e}")
                raise

    def can_send(self, addr: int, data: bytes, bus: int = 0):
        """Send a standard 11-bit CAN frame."""
        if not isinstance(addr, int) or not 0 <= addr <= 0x7FF or not 1 <= len(data) <= 8:
            raise ValueError("Expected a classic 11-bit CAN frame with 1..8 bytes")
        if bus != self.bus_num:
            raise ValueError("Unexpected CAN bus number")
        if not self.bus:
            raise RuntimeError("CAN bus not open")
        if self._can is not None:
            msg = self._can.Message(
                arbitration_id=addr,
                data=data,
                is_extended_id=False
            )
        else:
            # Lightweight duck-typed container if python-can not installed
            class _DuckCanMsg:
                def __init__(self, aid, d):
                    self.arbitration_id = aid
                    self.data = d
                    self.is_extended_id = False
            msg = _DuckCanMsg(addr, data)
        self.bus.send(msg)

    def write_frame(self, addr: int, data: bytes):
        """Helper alias matching J2534Device interface."""
        self.can_send(addr, data, self.bus_num)

    def can_recv(self, timeout_ms: int = 20) -> List[Tuple[int, bytes, int]]:
        """
        Receive pending CAN messages within timeout_ms.
        Returns list of (arbitration_id, data_bytes, bus_number).
        """
        if not self.bus:
            return []

        results: List[Tuple[int, bytes, int]] = []
        timeout_sec = max(0.001, timeout_ms / 1000.0)
        start = time.monotonic()

        # Get at least one message if available within timeout
        msg = self.bus.recv(timeout=timeout_sec)
        if msg is not None:
            if self._valid_message(msg):
                results.append((msg.arbitration_id, bytes(msg.data), self.bus_num))

            # Drain any additional queued messages without waiting
            while time.monotonic() - start < timeout_sec:
                extra = self.bus.recv(timeout=0.0)
                if extra is not None:
                    if self._valid_message(extra):
                        results.append((extra.arbitration_id, bytes(extra.data), self.bus_num))
                else:
                    break

        return results

    @staticmethod
    def _valid_message(msg):
        # gs_usb reports our own transmitted frames back as is_rx=False echoes.
        return (getattr(msg, 'is_rx', True)
                and not any(getattr(msg, flag, False) for flag in
                            ('is_extended_id', 'is_error_frame', 'is_remote_frame', 'is_fd'))
                and 0 <= msg.arbitration_id <= 0x7FF and 1 <= len(msg.data) <= 8)

    def can_clear(self, mask: int = 0xFFFF):
        """Drain any unread messages from socket buffer."""
        if not self.bus:
            return
        deadline = time.monotonic() + 0.1
        while time.monotonic() < deadline:
            msg = self.bus.recv(timeout=0.0)
            if msg is None:
                return
        raise RuntimeError("CAN receive queue did not drain within 100ms")

    def close(self):
        """Shut down the CAN bus interface."""
        if self.bus:
            try:
                self.bus.shutdown()
            except Exception:
                pass
            self.bus = None
