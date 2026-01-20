
import subprocess


def run_cmd(args):
    try:
        p = subprocess.run(args, capture_output=True, text=True)
        return p.stdout + "\n" + p.stderr
    except Exception as e:
        return str(e)

def get_usb_phy():
    # Find phy for wlan1 or usb adapter
    # reliable way: iterate /sys/class/net
    # simple way: assume wlan1 for this environment if it exists
    out = run_cmd(["iw", "dev"])
    return out

def full_scan():
    print("=== IW DEV ===")
    print(run_cmd(["iw", "dev"]))
    print("\n=== USB CONFIG ===")
    print(run_cmd(["lsusb"]))
    
    # Try to find the phy for wlan1
    iw_dev = run_cmd(["iw", "dev"])
    target_phy = None
    for line in iw_dev.splitlines():
        if "phy#" in line:
            curr = line.split("#")[1]
            target_phy = f"phy{curr}"
        if "Interface wlan1" in line and target_phy:
             print(f"\n=== CAPABILITIES FOR {target_phy} (wlan1) ===")
             print(run_cmd(["iw", target_phy, "info"]))
             return

    # If wlan1 not found, just dump all phys
    print("\n=== ALL PHYS INFO ===")
    try:
        phys = [l.split("#")[1] for l in run_cmd(["iw", "dev"]).splitlines() if "phy#" in l]
        for p in phys:
             print(f"--- phy{p} ---")
             print(run_cmd(["iw", f"phy{p}", "info"]))
    except:
        pass

if __name__ == "__main__":
    full_scan()
