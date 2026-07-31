import os
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext
from PIL import ImageTk, Image

# Path to your analyzer script — adjust the filename if yours differs
MAIN_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "V2XPerformanceAnalyzer.py")

# Output files your analyzer writes (working directory, hardcoded names).
# Keep this list in sync with whatever your analyzer actually produces.
OUTPUT_FILES = ["v2x_report.pdf", "v2x_report.docx", "trail_map.html"]


def resource_path(relative_path):
    try:
        base_path = sys._MEIPASS
    except AttributeError:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


class V2XAnalyzerGUI:
    def __init__(self, root):
        self.root = root
        root.title("C-V2X Network Performance Analyzer")
        root.geometry("780x820")
        root.config(background="#e9f8e3")
 # ---- In-window logo ----
        try:
            logo_img = Image.open(resource_path("NIST_CTL_logo.png")).resize((160, 29))
            self.logo = ImageTk.PhotoImage(logo_img)   # keep a reference on self
            logo_label = tk.Label(root, image=self.logo, bg="#e9f8e3")
            logo_label.place(x=8, y=8)
        except Exception as e:
            print("Logo load failed:", e)

        self.tx_path = tk.StringVar(value="Select a PDML file")
        self.rx_path = tk.StringVar(value="Select a PDML file")
        self.address = tk.StringVar(value="")

        # spatial analysis (map + distance graph) is optional
        self.spatial_enabled = tk.BooleanVar(value=False)
        self.rx_lat = tk.StringVar(value="")
        self.rx_lon = tk.StringVar(value="")

        # track where outputs landed after a successful run
        self.last_output_dir = None

        root.columnconfigure(0, weight=0)
        root.columnconfigure(1, weight=1)
        root.columnconfigure(2, weight=0)
        root.rowconfigure(9, weight=2)

        # Title
        tk.Label(root, text="C-V2X Network Performance Analyzer",
                 font=("Calibri", 17), bg="#e9f8e3").grid(
                 row=0, column=0, columnspan=3, padx=8, pady=12)

        # Transmitted file row
        tk.Label(root, text="Transmitted PDML", font=("Calibri", 12), bg="#e9f8e3").grid(
            row=1, column=0, padx=8, pady=8, sticky="w")
        tk.Entry(root, textvariable=self.tx_path, width=55).grid(
            row=1, column=1, padx=4, pady=8, sticky="ew")
        self.browse_tx = tk.Button(root, text="Browse...", command=self.browse_tx)
        self.browse_tx.grid(row=1, column=2, padx=8, pady=8, sticky="w")
        self.add_hover_effect(self.browse_tx, "#f0f0f0", "#E2E2E2")

        # Received file row 
        tk.Label(root, text="Received PDML", font=("Calibri", 12), bg="#e9f8e3").grid(
            row=2, column=0, padx=8, pady=8, sticky="w")
        tk.Entry(root, textvariable=self.rx_path, width=55).grid(
            row=2, column=1, padx=4, pady=8, sticky="ew")
        self.browse_rx = tk.Button(root, text="Browse...", command=self.browse_rx)
        self.browse_rx.grid(row=2, column=2, padx=8, pady=8, sticky="w")
        self.add_hover_effect(self.browse_rx, "#f0f0f0", "#E2E2E2")

        # optional address row 
        tk.Label(root, text="Tx MAC / IPv6\n(optional)", font=("Calibri", 11), bg="#e9f8e3",
                 justify="left").grid(row=3, column=0, padx=8, pady=8, sticky="w")
        tk.Entry(root, textvariable=self.address, width=55).grid(
            row=3, column=1, padx=4, pady=8, sticky="ew")
        tk.Label(root, text="Only for combined Tx files", font=("Calibri", 9),
                 fg="#555555", bg="#e9f8e3").grid(row=4, column=1, padx=4, sticky="w")

        # spatial analysis checkbox
        self.spatial_check = tk.Checkbutton(
            root, text="Generate trail map + distance graph", font=("Calibri", 11),
            bg="#e9f8e3", variable=self.spatial_enabled, command=self.toggle_spatial,
            activebackground="#e9f8e3")
        self.spatial_check.grid(row=5, column=0, columnspan=3, padx=8, pady=(12, 0), sticky="w")

        # coordinate fields
        self.coord_frame = tk.Frame(root, bg="#e9f8e3")
        tk.Label(self.coord_frame, text="Receiver Lat:", font=("Calibri", 10),
                 bg="#e9f8e3").grid(row=0, column=0, padx=(24, 4))
        tk.Entry(self.coord_frame, textvariable=self.rx_lat, width=14).grid(row=0, column=1, padx=4)
        tk.Label(self.coord_frame, text="Lon:", font=("Calibri", 10),
                 bg="#e9f8e3").grid(row=0, column=2, padx=4)
        tk.Entry(self.coord_frame, textvariable=self.rx_lon, width=14).grid(row=0, column=3, padx=4)
        self.coord_frame.grid(row=6, column=0, columnspan=3, sticky="w", pady=(0, 4))
        self.coord_frame.grid_remove()   # start hidden

        # run button
        self.run_btn = tk.Button(
            root, text="Run Analysis", font=("Calibri", 16, "bold"),
            command=self.run_analysis, bg="#005EA2", fg="white", height=1, width=16)
        self.run_btn.grid(row=7, column=0, columnspan=3, pady=12)
        self.add_hover_effect(self.run_btn, "#005EA2", "#1A4480")

        # output box
        tk.Label(root, text="Result:", font=("Calibri", 12), bg="#e9f8e3").grid(
            row=8, column=0, padx=8, sticky="w")
        self.output_box = scrolledtext.ScrolledText(root, wrap=tk.WORD, width=90, height=22)
        self.output_box.grid(row=9, column=0, columnspan=3, rowspan=2, padx=8, pady=4, sticky="nsew")
        self.output_box.config(background="#f3f5fa")

        # bottom buttons
        self.open_btn = tk.Button(root, text="Open Report Folder", command=self.open_output_folder,
                                  state=tk.DISABLED)
        self.open_btn.grid(row=11, column=0, columnspan=3, pady=8)
        self.add_hover_effect(self.open_btn, "#f0f0f0", "#F9F9F9")

    # spatial toggle
    def toggle_spatial(self):
        if self.spatial_enabled.get():
            self.coord_frame.grid()       # show coordinate fields
        else:
            self.coord_frame.grid_remove()  # hide them

    # file pickers
    def browse_tx(self):
        path = filedialog.askopenfilename(
            filetypes=[("PDML files", "*.pdml"), ("All files", "*.*")])
        if path:
            self.tx_path.set(path)

    def browse_rx(self):
        path = filedialog.askopenfilename(
            filetypes=[("PDML files", "*.pdml"), ("All files", "*.*")])
        if path:
            self.rx_path.set(path)

    def run_analysis(self):
        tx = self.tx_path.get().strip()
        rx = self.rx_path.get().strip()
        addr = self.address.get().strip()

        if tx == "Select a PDML file" or rx == "Select a PDML file":
            messagebox.showwarning("Missing files", "Please select both PDML files first.")
            return
        if not os.path.isfile(tx) or not os.path.isfile(rx):
            messagebox.showerror("File not found", "One or both of the selected files no longer exist.")
            return
        if not os.path.isfile(MAIN_SCRIPT):
            messagebox.showerror("Analyzer not found",
                                 f"Could not find:\n{MAIN_SCRIPT}\n\n"
                                 "Edit MAIN_SCRIPT at the top of this file to point to your analyzer.")
            return

        # -u forces unbuffered output so progress streams live
        cmd = [sys.executable, "-u", MAIN_SCRIPT, tx, rx]
        if addr:
            cmd.append(addr)

        # spatial analysis: validate coordinates before passing them through
        if self.spatial_enabled.get():
            lat = self.rx_lat.get().strip()
            lon = self.rx_lon.get().strip()
            if not lat or not lon:
                messagebox.showwarning(
                    "Missing coordinates",
                    "Receiver latitude and longitude are required for the map and distance graph.")
                return
            try:
                float(lat)
                float(lon)
            except ValueError:
                messagebox.showwarning(
                    "Invalid coordinates",
                    "Latitude and longitude must be numbers (decimal degrees).")
                return
            # passed as a --rx-location flag — adjust to match your analyzer's argument
            cmd.extend(["--rx-location", f"{lat},{lon}"])

        self.output_box.delete("1.0", tk.END)
        self.run_btn.config(state=tk.DISABLED)
        self.root.update_idletasks()

        # run the analyzer from the Tx file's folder so outputs land somewhere predictable
        workdir = os.path.dirname(os.path.abspath(tx))

        # stream output line by line so the user sees progress as it happens
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, cwd=workdir)
        except Exception as e:
            self.run_btn.config(state=tk.NORMAL)
            messagebox.showerror("Error running analyzer", str(e))
            return

        for line in proc.stdout:
            self.output_box.insert(tk.END, line)
            self.output_box.see(tk.END)       # auto-scroll to newest line
            self.root.update_idletasks()      # let the GUI repaint

        proc.wait()
        self.run_btn.config(state=tk.NORMAL)

        if proc.returncode != 0:
            self.output_box.insert(tk.END,
                "\n\n" + "=" * 50 + "\n"
                "ERROR: the analyzer exited with a non-zero status.\n"
                "See the output above for details.")
            return

        # report where the output files went
        self.last_output_dir = workdir
        produced = [f for f in OUTPUT_FILES if os.path.isfile(os.path.join(workdir, f))]

        self.output_box.insert(tk.END, "\n\n" + "=" * 50 + "\n")
        if produced:
            self.output_box.insert(tk.END, f"Output files saved to:\n{workdir}\n\n")
            for f in produced:
                self.output_box.insert(tk.END, f"  - {f}\n")
            self.open_btn.config(state=tk.NORMAL)
        else:
            self.output_box.insert(tk.END,
                "Analysis finished, but no output files were found.\n"
                "Check that the analyzer writes its report to the working directory.")
        self.output_box.see(tk.END)

    # open folder
    def open_output_folder(self):
        if not self.last_output_dir or not os.path.isdir(self.last_output_dir):
            messagebox.showinfo("No folder", "Run an analysis first.")
            return
        # platform-appropriate folder open
        if sys.platform.startswith("win"):
            os.startfile(self.last_output_dir)
        elif sys.platform == "darwin":
            subprocess.run(["open", self.last_output_dir])
        else:
            subprocess.run(["xdg-open", self.last_output_dir])

    # ---------- hover ----------
    def add_hover_effect(self, button, normal_color, hover_color):
        button.bind("<Enter>", lambda e: button.config(bg=hover_color))
        button.bind("<Leave>", lambda e: button.config(bg=normal_color))


if __name__ == "__main__":
    root = tk.Tk()
    app = V2XAnalyzerGUI(root)
    root.mainloop()