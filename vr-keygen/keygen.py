import os
import subprocess
import time
import customtkinter as ctk
import pygame

# --- SETTINGS ---
ENV_FILE = "/etc/vr-hotspot/env"
TRACKER_FILE = "music.xm"

class VRKeygen(ctk.CTk):
    def __init__(self):
        super().__init__()

        # 1. Window Configuration
        self.title("VR-HOTSPOTD // KEYGEN")
        self.geometry("520x380")
        self.configure(fg_color="#050505")
        self.attributes("-topmost", True)

        # 2. Retro Audio (Runs as user, so it will work!)
        pygame.mixer.init()
        if os.path.exists(TRACKER_FILE):
            try:
                pygame.mixer.music.load(TRACKER_FILE)
                pygame.mixer.music.play(-1)
            except Exception as e:
                print(f"Music error: {e}")

        # 3. UI Layout
        self.hdr = ctk.CTkLabel(self, text="ᯤ VRHotspot Keygen ᯤ",
                                font=("Courier", 24, "bold"), text_color="#00FF00")
        self.hdr.pack(pady=(30, 5))

        self.sub = ctk.CTkLabel(self, text="for the VR community by @josethevrtech",
                                font=("Courier", 12), text_color="#008800")
        self.sub.pack()

        self.token_var = ctk.StringVar(value="--- STANDBY ---")
        self.entry = ctk.CTkEntry(self, textvariable=self.token_var, width=440, height=50,
                                 font=("Courier", 16), justify="center",
                                 fg_color="#111", text_color="#00FF00", border_color="#00FF00")
        self.entry.pack(pady=40)

        self.btn = ctk.CTkButton(self, text="GENERATE ACCESS TOKEN", corner_radius=0,
                                 fg_color="#004400", hover_color="#00FF00",
                                 text_color="white", font=("Courier", 14, "bold"),
                                 command=self.fetch_token)
        self.btn.pack()

        self.footer = ctk.CTkLabel(self, text="STREAMS: UDP PRIORITY // 6GHz CAPABLE",
                                  font=("Courier", 10), text_color="#004400")
        self.footer.pack(side="bottom", pady=20)

    def fetch_token(self):
        """Runs the awk command via sudo only for this specific action."""
        # This command specifically targets the token in your env file
        cmd = f"sudo awk -F= '($1==\"VR_HOTSPOTD_API_TOKEN\"){{gsub(/\\r/,\"\",$2); print $2; exit}}' {ENV_FILE}"

        try:
            # We use check_output to get the token back from the sudo command
            token = subprocess.check_output(cmd, shell=True).decode().strip()

            if token:
                self.animate_token(token)
            else:
                self.token_var.set("TOKEN NOT FOUND")
        except Exception:
            self.token_var.set("PERMISSION DENIED")

    def animate_token(self, final_token):
        """Adds that old-school keygen 'typing' effect."""
        def type_effect():
            for i in range(len(final_token) + 1):
                self.token_var.set(final_token[:i])
                time.sleep(0.05)

        import threading
        threading.Thread(target=type_effect).start()

if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    app = VRKeygen()
    app.mainloop()
