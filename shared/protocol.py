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
    return crc


def encode_message(
    device_id: str,
    msg_type: MessageType,
    count_in: int,
    count_out: int,
    timestamp: Optional[float] = None,
) -> bytes:
    if len(device_id) > 8:
        raise ValueError("device_id must be 8 characters or fewer")

    if timestamp is None:
        timestamp = time.time()

    device_id_bytes = device_id.encode("ascii").ljust(8)[:8]
    ts_int = int(timestamp)

    payload = struct.pack(
        ">B8sBHHI",
        PROTOCOL_VERSION,
        device_id_bytes,
        int(msg_type),
        count_in,
        count_out,
        ts_int,
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

    version, device_id_bytes, msg_type_raw, count_in, count_out, ts_int = struct.unpack(
        ">B8sBHHI", payload
    )

    if version != PROTOCOL_VERSION:
        raise ValueError(f"Unsupported protocol version: {version}")

    device_id = device_id_bytes.decode("ascii").rstrip()

    return {
        "protocol_version": version,
        "device_id": device_id,
        "msg_type": MessageType(msg_type_raw),
        "count_in": count_in,
        "count_out": count_out,
        "timestamp": float(ts_int),
    }
