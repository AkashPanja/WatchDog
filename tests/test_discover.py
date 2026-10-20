"""WS-Discovery parsing/probing tests (no real network)."""

from __future__ import annotations

from app import discover

PROBE_MATCH = b"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl"
            xmlns:tds="http://www.onvif.org/ver10/device/wsdl">
  <s:Header>
    <a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/ProbeMatches</a:Action>
    <a:RelatesTo>uuid:probe-1</a:RelatesTo>
  </s:Header>
  <s:Body>
    <d:ProbeMatches>
      <d:ProbeMatch>
        <a:EndpointReference><a:Address>urn:uuid:device-1</a:Address></a:EndpointReference>
        <d:Types>dn:NetworkVideoTransmitter tds:Device</d:Types>
        <d:Scopes>onvif://www.onvif.org/type/video_encoder onvif://www.onvif.org/name/Front%20Door onvif://www.onvif.org/hardware/DS-2CD2143G2-I</d:Scopes>
        <d:XAddrs>http://[fe80::1]:80/onvif/device_service http://192.168.1.64:8000/onvif/device_service</d:XAddrs>
        <d:MetadataVersion>1</d:MetadataVersion>
      </d:ProbeMatch>
    </d:ProbeMatches>
  </s:Body>
</s:Envelope>
"""


def test_parse_response_extracts_camera() -> None:
    entries = discover.parse_response(PROBE_MATCH)
    assert entries == [
        {
            "host": "192.168.1.64",
            "onvif_port": 8000,
            "name": "Front Door",
            "hardware": "DS-2CD2143G2-I",
            "xaddr": "http://192.168.1.64:8000/onvif/device_service",
        }
    ]


def test_parse_response_rejects_garbage() -> None:
    assert discover.parse_response(b"not xml at all") == []


class FakeSocket:
    def __init__(self, *args, **kwargs) -> None:
        self.sent: list[tuple] = []
        self.responses: list[tuple[bytes, tuple]] = []

    def setsockopt(self, *args) -> None:
        pass

    def bind(self, addr) -> None:
        pass

    def sendto(self, data: bytes, addr: tuple) -> int:
        self.sent.append(addr)
        return len(data)

    def settimeout(self, timeout: float) -> None:
        pass

    def recvfrom(self, size: int) -> tuple[bytes, tuple]:
        if self.responses:
            return self.responses.pop(0)
        raise TimeoutError()

    def close(self) -> None:
        pass


def test_discover_devices_probes_and_dedupes(monkeypatch) -> None:
    fake = FakeSocket()
    fake.responses = [
        (PROBE_MATCH, ("192.168.1.64", 3702)),
        (PROBE_MATCH, ("192.168.1.64", 3702)),
    ]
    monkeypatch.setattr(discover.socket, "socket", lambda *args, **kwargs: fake)

    devices = discover.discover_devices(timeout=1.0)

    assert devices == [
        {
            "host": "192.168.1.64",
            "onvif_port": 8000,
            "name": "Front Door",
            "hardware": "DS-2CD2143G2-I",
            "xaddr": "http://192.168.1.64:8000/onvif/device_service",
        }
    ]
    assert ("239.255.255.250", 3702) in fake.sent
    assert ("255.255.255.255", 3702) in fake.sent


def test_discover_devices_timeout_clamped(monkeypatch) -> None:
    fake = FakeSocket()
    monkeypatch.setattr(discover.socket, "socket", lambda *args, **kwargs: fake)

    assert discover.discover_devices(timeout=999) == []
    assert fake.sent  # probes still went out
