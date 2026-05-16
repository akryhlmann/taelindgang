import struct
import json
import time
from enum import IntEnum
from typing import Optional

PROTOCOL_VERSION = 1
MAX_PAYLOAD_SIZE = 200

class MessageType(IntEnum):
    HEARTBEAT = 1
    COUNT_UPDATE = 2
    RESET = 3

def _crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF

def encode_message(
    device_id: str,
    msg_type: MessageType,
    count_in: int,
    count_out: int,
    timestamp: float,
) -> bytes:
    device_id_bytes = device_id.encode("ascii")[:8].ljust(8, b"\x00")
    timestamp_int = int(timestamp)
    payload = struct.pack(
        ">B8sBHHI",
        PROTOCOL_VERSION,
        device_id_bytes,
        int(msg_type),
        count_in,
        count_out,
        timestamp_int,
    )
    crc = _crc16(payload)
    return payload + struct.pack(">H", crc)

def decode_message(data: bytes) -> dict:
    if len(data) < 18:
        raise ValueError(f"Packet too short: {len(data)} bytes")

    payload = data[:-2]
    received_crc = struct.unpack(">H", data[-2:])[0]
    computed_crc = _crc16(payload)
    if received_crc != computed_crc:
        raise ValueError(
            f"CRC mismatch: received {received_crc:#06x}, computed {computed_crc:#06x}"
        )

    version, device_id_bytes, msg_type_val, count_in, count_out, timestamp_int = (
        struct.unpack(">B8sBHHI", payload)
    )

    if version != PROTOCOL_VERSION:
        raise ValueError(f"Unknown protocol version: {version}")

    device_id = device_id_bytes.rstrip(b"\x00").decode("ascii")

    return {
        "version": version,
        "device_id": device_id,
        "msg_type": MessageType(msg_type_val),
        "count_in": count_in,
        "count_out": count_out,
        "timestamp": float(timestamp_int),
    }
