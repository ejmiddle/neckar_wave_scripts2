import csv
import subprocess
import time
import socket
import urllib.request
from datetime import datetime
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

# --------------------------------------------------------------
# 1) Monitoring-Funktion: Ping-Messungen in CSV loggen
# --------------------------------------------------------------

def get_default_gateway():
    """Try to detect the default gateway on macOS/Linux."""
    # macOS: route -n get default | grep gateway
    try:
        result = subprocess.run(
            ["route", "-n", "get", "default"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if "gateway:" in line:
                    return line.split("gateway:")[1].strip()
    except Exception:
        pass

    # Linux: ip route | head -1
    try:
        result = subprocess.run(
            ["ip", "route"],
            capture_output=True,
            text=True
        )
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if line.startswith("default "):
                    parts = line.split()
                    if "via" in parts:
                        return parts[parts.index("via") + 1]
    except Exception:
        pass

    return None


def dns_lookup_ms(domain):
    start = time.perf_counter()
    try:
        socket.getaddrinfo(domain, 443)
        elapsed = (time.perf_counter() - start) * 1000
        return elapsed, 0
    except Exception:
        return None, 1


def https_request_ms(url):
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            response.read(1)
        elapsed = (time.perf_counter() - start) * 1000
        return elapsed, 0
    except Exception:
        return None, 1


def run_monitoring(
    public_target="8.8.8.8",
    gateway_target=None,
    dns_domain="google.com",
    https_url="https://www.google.com/generate_204",
    interval=2,
    duration_hours=6,
    logfile="ping_log.csv"
):
    num_tests = int((duration_hours * 3600) / interval)
    if gateway_target is None:
        gateway_target = get_default_gateway()

    with open(logfile, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp",
            "gateway_latency_ms",
            "gateway_packet_loss",
            "public_latency_ms",
            "public_packet_loss",
            "dns_lookup_ms",
            "dns_failed",
            "https_latency_ms",
            "https_failed"
        ])

        for _ in range(num_tests):
            timestamp = datetime.now().isoformat()
            gateway_latency = ping_once(gateway_target) if gateway_target else None
            public_latency = ping_once(public_target)
            dns_ms, dns_failed = dns_lookup_ms(dns_domain)
            https_ms, https_failed = https_request_ms(https_url)

            writer.writerow([
                timestamp,
                gateway_latency,
                1 if gateway_latency is None else 0,
                public_latency,
                1 if public_latency is None else 0,
                dns_ms,
                dns_failed,
                https_ms,
                https_failed
            ])

            status = []
            if gateway_latency is None:
                status.append("GW LOSS")
            else:
                status.append(f"GW {gateway_latency} ms")
            if public_latency is None:
                status.append("PUB LOSS")
            else:
                status.append(f"PUB {public_latency} ms")
            if dns_failed:
                status.append("DNS FAIL")
            else:
                status.append(f"DNS {int(dns_ms)} ms")
            if https_failed:
                status.append("HTTPS FAIL")
            else:
                status.append(f"HTTPS {int(https_ms)} ms")
            print(timestamp, "|", ", ".join(status))

            time.sleep(interval)


def ping_once(target):
    """Sendet einen Ping und extrahiert die Latenz."""
    if not target:
        return None
    result = subprocess.run(
        ["ping", "-c", "1", target],
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        return None

    for line in result.stdout.split("\n"):
        if "time=" in line:
            try:
                return float(line.split("time=")[1].split(" ")[0])
            except:
                return None
    return None


# --------------------------------------------------------------
# 2) Visualisieren: Mehrere Plots erzeugen
# --------------------------------------------------------------

def plot_results(logfile="ping_log.csv", output_path=None):
    df = pd.read_csv(logfile)

    # Zeitspalte konvertieren
    df["timestamp"] = pd.to_datetime(df["timestamp"])

    analyze_results(df)

    fig, axes = plt.subplots(3, 1, figsize=(12, 9), sharex=True)

    # Plot 1: Latenz über Zeit (Gateway + Public)
    ax = axes[0]
    if "gateway_latency_ms" in df.columns:
        ax.plot(df["timestamp"], df["gateway_latency_ms"], marker=".", linestyle="-", label="Gateway", alpha=0.8)
    if "public_latency_ms" in df.columns:
        ax.plot(df["timestamp"], df["public_latency_ms"], marker=".", linestyle="-", label="Public", alpha=0.8)
    ax.set_title("Latenz über Zeit")
    ax.set_ylabel("Latenz (ms)")
    ax.legend()
    ax.grid(True)

    # Plot 2: Paketverlust über Zeit
    ax = axes[1]
    if "gateway_packet_loss" in df.columns:
        ax.plot(df["timestamp"], df["gateway_packet_loss"], color="orange", linestyle="--", label="Gateway")
    if "public_packet_loss" in df.columns:
        ax.plot(df["timestamp"], df["public_packet_loss"], color="red", linestyle="--", label="Public")
    ax.set_title("Paketverlust über Zeit")
    ax.set_ylabel("Paketverlust (0/1)")
    ax.legend()
    ax.grid(True)

    # Plot 3: DNS + HTTPS Latenzen
    ax = axes[2]
    if "dns_lookup_ms" in df.columns:
        ax.plot(df["timestamp"], df["dns_lookup_ms"], color="blue", linestyle="-", label="DNS")
    if "https_latency_ms" in df.columns:
        ax.plot(df["timestamp"], df["https_latency_ms"], color="green", linestyle="-", label="HTTPS")
    ax.set_title("DNS/HTTPS Latenzen")
    ax.set_xlabel("Zeit")
    ax.set_ylabel("Latenz (ms)")
    ax.legend()
    ax.grid(True)

    # Time axis formatting (works for short and long spans)
    locator = mdates.AutoDateLocator(minticks=3, maxticks=10)
    formatter = mdates.ConciseDateFormatter(locator)
    axes[2].xaxis.set_major_locator(locator)
    axes[2].xaxis.set_major_formatter(formatter)

    fig.tight_layout()

    # Plot 4: Histogramm der Public-Latenzen (separate figure)
    valid_latencies = df["public_latency_ms"].dropna() if "public_latency_ms" in df.columns else df["latency_ms"].dropna()
    fig_hist, ax_hist = plt.subplots(figsize=(8, 4))
    ax_hist.hist(valid_latencies, bins=40)
    ax_hist.set_title("Verteilung der Latenzen (Public)")
    ax_hist.set_xlabel("Latenz (ms)")
    ax_hist.set_ylabel("Häufigkeit")
    ax_hist.grid(True)
    fig_hist.tight_layout()

    if output_path:
        fig.savefig(output_path, dpi=150)
        hist_path = output_path.replace(".png", "_hist.png")
        fig_hist.savefig(hist_path, dpi=150)
    else:
        plt.show()


def analyze_results(df):
    """Print a lightweight analysis for mentionable errors."""
    rows = len(df)
    if rows == 0:
        print("Analysis: no data rows found.")
        return

    issues = []
    loss_cols = {
        "gateway_packet_loss": "Gateway packet loss",
        "public_packet_loss": "Public packet loss",
        "dns_failed": "DNS failures",
        "https_failed": "HTTPS failures",
    }
    for col, label in loss_cols.items():
        if col in df.columns:
            count = int(df[col].sum())
            if count > 0:
                issues.append(f"{label}: {count}/{rows} ({(count/rows)*100:.1f}%)")

    # Detect large time gaps vs median sampling interval
    if "timestamp" in df.columns:
        deltas = df["timestamp"].sort_values().diff().dt.total_seconds().dropna()
        if len(deltas) > 0:
            median_interval = deltas.median()
            gap_threshold = max(median_interval * 2.5, median_interval + 5)
            gap_count = int((deltas > gap_threshold).sum())
            if gap_count > 0:
                issues.append(f"Sampling gaps: {gap_count} gaps > {gap_threshold:.1f}s")

    # Latency spikes (public) using a robust threshold
    if "public_latency_ms" in df.columns:
        lat = df["public_latency_ms"].dropna()
        if len(lat) > 0:
            median = lat.median()
            mad = (lat - median).abs().median()
            spike_threshold = median + max(3 * mad, 50)
            spike_count = int((lat > spike_threshold).sum())
            if spike_count > 0:
                issues.append(f"Public latency spikes: {spike_count} samples > {spike_threshold:.1f} ms")

    if issues:
        print("Analysis: mentionable issues detected:")
        for item in issues:
            print(f"  - {item}")
    else:
        print("Analysis: no mentionable issues detected.")


# --------------------------------------------------------------
# Example Run
# --------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Internet ping test: monitor latency/loss and plot results."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    monitor_parser = subparsers.add_parser("monitor", help="Run ping monitoring")
    monitor_parser.add_argument("--public-target", default="8.8.8.8")
    monitor_parser.add_argument("--gateway", default=None)
    monitor_parser.add_argument("--dns-domain", default="google.com")
    monitor_parser.add_argument("--https-url", default="https://www.google.com/generate_204")
    monitor_parser.add_argument("--interval", type=int, default=2, help="Seconds between tests")
    monitor_parser.add_argument("--duration-hours", type=float, default=6)
    monitor_parser.add_argument("--logfile", default="ping_log.csv")

    plot_parser = subparsers.add_parser("plot", help="Plot results from a logfile")
    plot_parser.add_argument("--logfile", default="ping_log.csv")
    plot_parser.add_argument("--output", default=None)

    args = parser.parse_args()

    if args.command == "monitor":
        run_monitoring(
            public_target=args.public_target,
            gateway_target=args.gateway,
            dns_domain=args.dns_domain,
            https_url=args.https_url,
            interval=args.interval,
            duration_hours=args.duration_hours,
            logfile=args.logfile
        )
    elif args.command == "plot":
        plot_results(logfile=args.logfile, output_path=args.output)
