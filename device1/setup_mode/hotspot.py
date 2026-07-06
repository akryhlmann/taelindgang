"""
WiFi hotspot management for setup mode.

Uses NetworkManager (nmcli) to create an access point, then:
  - Writes a dnsmasq snippet to capture all DNS → hotspot IP (captive portal)
  - Adds an iptables REDIRECT rule: port 80 → 8080 (where Flask runs)

Requires: NetworkManager ≥ 1.20, iptables
"""
import logging
import os
import subprocess
import time

logger = logging.getLogger(__name__)

HOTSPOT_IP = "10.42.0.1"
_CONN_NAME = "borneland-setup"
_IFACE = "wlan0"
_FLASK_PORT = 8080
_DNSMASQ_CONF = "/etc/NetworkManager/dnsmasq-shared.d/captive-portal.conf"
_IPTABLES_COMMENT = "borneland-setup"


def _run(cmd: list, check: bool = True) -> subprocess.CompletedProcess:
    logger.debug("run: %s", " ".join(str(c) for c in cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {' '.join(str(c) for c in cmd)}\n"
            f"stderr: {result.stderr.strip()}"
        )
    return result


def start(ssid: str, password: str) -> None:
    """Bring up WiFi hotspot with captive portal redirect."""
    _write_dnsmasq_conf()
    _delete_connection()
    _create_hotspot(ssid, password)
    time.sleep(3)  # Let NM finish setting up the interface
    _iptables_add()
    logger.info("Hotspot up — SSID=%r  IP=%s", ssid, HOTSPOT_IP)


def stop() -> None:
    """Tear down hotspot and remove iptables rule."""
    _iptables_remove()
    _delete_connection()
    _remove_dnsmasq_conf()
    logger.info("Hotspot stopped")


# ── internal helpers ─────────────────────────────────────────────────────────

def _write_dnsmasq_conf() -> None:
    """Tell NetworkManager's embedded dnsmasq to send all DNS queries to our IP."""
    os.makedirs(os.path.dirname(_DNSMASQ_CONF), exist_ok=True)
    with open(_DNSMASQ_CONF, "w") as f:
        f.write(f"address=/#/{HOTSPOT_IP}\n")
    logger.debug("Wrote %s", _DNSMASQ_CONF)


def _remove_dnsmasq_conf() -> None:
    try:
        os.remove(_DNSMASQ_CONF)
        logger.debug("Removed %s", _DNSMASQ_CONF)
    except FileNotFoundError:
        pass


def _delete_connection() -> None:
    result = _run(["nmcli", "connection", "show", _CONN_NAME], check=False)
    if result.returncode == 0:
        _run(["nmcli", "connection", "delete", _CONN_NAME], check=False)


def _create_hotspot(ssid: str, password: str) -> None:
    _run([
        "nmcli", "device", "wifi", "hotspot",
        "ifname", _IFACE,
        "ssid", ssid,
        "password", password,
        "con-name", _CONN_NAME,
    ])


def _iptables_add() -> None:
    _run([
        "iptables", "-t", "nat", "-A", "PREROUTING",
        "-i", _IFACE, "-p", "tcp", "--dport", "80",
        "-j", "REDIRECT", "--to-port", str(_FLASK_PORT),
        "-m", "comment", "--comment", _IPTABLES_COMMENT,
    ], check=False)


def _iptables_remove() -> None:
    _run([
        "iptables", "-t", "nat", "-D", "PREROUTING",
        "-i", _IFACE, "-p", "tcp", "--dport", "80",
        "-j", "REDIRECT", "--to-port", str(_FLASK_PORT),
        "-m", "comment", "--comment", _IPTABLES_COMMENT,
    ], check=False)
