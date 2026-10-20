"""ONVIF WS-Discovery: probe the local network for cameras."""

from __future__ import annotations

import socket
import time
import uuid
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlparse

_MULTICAST = ("239.255.255.250", 3702)
_BROADCAST = ("255.255.255.255", 3702)

_PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <s:Header>
    <a:Action s:mustUnderstand="1">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</a:Action>
    <a:MessageID>uuid:{message_id}</a:MessageID>
    <a:To s:mustUnderstand="1">urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To>
  </s:Header>
  <s:Body>
    <d:Probe>
      <d:Types>dn:NetworkVideoTransmitter</d:Types>
    </d:Probe>
  </s:Body>
</s:Envelope>
"""


def _probe_message() -> bytes:
    return _PROBE.format(message_id=uuid.uuid4()).encode()


def parse_response(data: bytes) -> list[dict]:
    """Parse one WS-Discovery ProbeMatch payload into camera entries."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []

    xaddrs: list[str] = []
    scopes = ""
    for element in root.iter():
        tag = element.tag.split("}")[-1]
        if tag == "XAddrs" and element.text:
            xaddrs.extend(element.text.split())
        elif tag == "Scopes" and element.text is not None:
            scopes = element.text

    name = ""
    hardware = ""
    for token in scopes.split():
        if "/name/" in token:
            name = unquote(token.split("/name/", 1)[1])
        elif "/hardware/" in token:
            hardware = unquote(token.split("/hardware/", 1)[1])

    entries: list[dict] = []
    for xaddr in xaddrs:
        parsed = urlparse(xaddr)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            continue
        if ":" in parsed.hostname:  # IPv6 literal - not usable for the host field
            continue
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        entries.append(
            {
                "host": parsed.hostname,
                "onvif_port": port,
                "name": name,
                "hardware": hardware,
                "xaddr": xaddr,
            }
        )
    return entries


def discover_devices(timeout: float = 3.0) -> list[dict]:
    """Probe the LAN via WS-Discovery and return unique ONVIF devices.

    Sends an ONVIF NetworkVideoTransmitter Probe to the discovery multicast
    group and the broadcast address, then collects ProbeMatch replies for
    ``timeout`` seconds. Safe to run in a worker thread.
    """
    timeout = max(0.5, min(float(timeout), 15.0))
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.bind(("", 0))

        message = _probe_message()
        for target in (_MULTICAST, _BROADCAST):
            try:
                sock.sendto(message, target)
            except OSError:
                pass

        deadline = time.monotonic() + timeout
        found: dict[tuple[str, int], dict] = {}
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            sock.settimeout(remaining)
            try:
                data, _addr = sock.recvfrom(65535)
            except (TimeoutError, OSError):
                break
            for entry in parse_response(data):
                found.setdefault((entry["host"], entry["onvif_port"]), entry)
        return sorted(found.values(), key=lambda entry: entry["host"])
    finally:
        sock.close()
