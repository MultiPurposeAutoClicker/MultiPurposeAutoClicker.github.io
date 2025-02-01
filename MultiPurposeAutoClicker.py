import os
import json
import tkinter as tk
import threading
import time
import keyboard
import customtkinter as ctk
import ctypes
import random
from ctypes import wintypes
from tkinter import filedialog, messagebox
import pystray
from PIL import Image
import sys
import winreg  # for registry manipulation

# Define the icon path once so we can use it everywhere.
ICON_PATH = r"C:\projects 2.0\MultiPurposeAutoClicker.ico"

# Validation function: allow only digits (or empty string)
def validate_numeric(P):
    return P == "" or P.isdigit()

# PATCH: Monkey patch CTkButton._draw to ignore TclError when its canvas has been destroyed.
original_ctkbutton_draw = ctk.CTkButton._draw
def patched_ctkbutton_draw(self, no_color_updates=False):
    try:
        original_ctkbutton_draw(self, no_color_updates=no_color_updates)
    except tk.TclError:
        pass
ctk.CTkButton._draw = patched_ctkbutton_draw

# A safe IntVar that ignores invalid values
class SafeIntVar(tk.IntVar):
    def set(self, value):
        try:
            super().set(int(float(value)))
        except (ValueError, tk.TclError):
            pass
    def get(self):
        try:
            return super().get()
        except (ValueError, tk.TclError):
            return 0

# Windows API setup for sending a left mouse click and setting the cursor position
SendInput = ctypes.windll.user32.SendInput
PUL = ctypes.POINTER(ctypes.c_ulong)
class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.DWORD),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", PUL)
    ]
class INPUT(ctypes.Structure):
    class _I(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT)]
    _anonymous_ = ("i",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("i", _I)
    ]
INPUT_MOUSE = 0
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP   = 0x0004
def send_left_click():
    ii = INPUT(type=INPUT_MOUSE)
    ii.mi.dwFlags = MOUSEEVENTF_LEFTDOWN
    SendInput(1, ctypes.byref(ii), ctypes.sizeof(ii))
    ii.mi.dwFlags = MOUSEEVENTF_LEFTUP
    SendInput(1, ctypes.byref(ii), ctypes.sizeof(ii))
def set_cursor_position(x, y):
    ctypes.windll.user32.SetCursorPos(x, y)
def get_mouse_position():
    class POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]
    pt = POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)

# Custom dialog for naming/renaming a profile
class ProfileNamerDialog(ctk.CTkToplevel):
    def __init__(self, parent, title_text="Enter profile name:", initial_value=""):
        super().__init__(parent)
        self.title("Profile Name")
        self.geometry("300x120")
        self.resizable(False, False)
        self.grab_set()  # Modal
        self.iconbitmap(ICON_PATH)
        self.profile_name = None
        self.label = ctk.CTkLabel(self, text=title_text)
        self.label.pack(pady=(10, 5), padx=10)
        self.entry = ctk.CTkEntry(self)
        self.entry.insert(0, initial_value)
        self.entry.pack(pady=5, padx=10, fill="x")
        button_frame = ctk.CTkFrame(self)
        button_frame.pack(pady=10)
        self.ok_button = ctk.CTkButton(button_frame, text="OK", command=self.on_ok)
        self.ok_button.pack(side="left", padx=(0, 5))
        self.cancel_button = ctk.CTkButton(button_frame, text="Cancel", command=self.on_cancel)
        self.cancel_button.pack(side="left", padx=(5, 0))
        self.entry.bind("<Return>", lambda event: self.on_ok())
        self.entry.bind("<Escape>", lambda event: self.on_cancel())
    def on_ok(self):
        name = self.entry.get().strip()
        if not name:
            messagebox.showerror("Error", "Profile name cannot be empty.")
            return
        if len(name) > 10:
            messagebox.showerror("Error", "Profile name must be 10 characters or less.")
            return
        self.profile_name = name
        self.destroy()
    def on_cancel(self):
        self.destroy()

def ask_profile_name(parent, title_text="Enter profile name:", initial_value=""):
    dialog = ProfileNamerDialog(parent, title_text, initial_value)
    parent.wait_window(dialog)
    return dialog.profile_name

# Main application class
class MinimalAutoClicker:
    def __init__(self):
        # Setup settings folder and file in %APPDATA%\MultiPurposeAutoClicker
        self.settings_dir = os.path.join(os.getenv("APPDATA"), "MultiPurposeAutoClicker")
        os.makedirs(self.settings_dir, exist_ok=True)
        self.settings_file = os.path.join(self.settings_dir, "settings.json")

        self.root = ctk.CTk()
        self.root.title("Multi-Purpose Auto Clicker")
        self.root.geometry("1000x500")
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")
        self.root.resizable(False, False)
        self.root.update_idletasks()
        self.root.iconbitmap(ICON_PATH)
        try:
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            if not hwnd:
                hwnd = self.root.winfo_id()
            GWL_STYLE = -16
            WS_MAXIMIZEBOX = 0x00010000
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_STYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_STYLE, style & ~WS_MAXIMIZEBOX)
        except Exception:
            pass

        # Tray icon object (None if not minimized)
        self.tray_icon = None

        # State variables
        self.is_clicking = False
        self.cps_var = SafeIntVar(value=10)
        self.hotkey = 'f8'
        self.hotkey_mode = ctk.StringVar(value="Click to Toggle")
        # Default modifier is "none". (For startup, all profiles must have a modifier other than "none")
        self.modifier_var = ctk.StringVar(value="none")
        # Create profile hotkey bindings dictionary early to avoid attribute errors in callbacks.
        self.profile_hotkey_bindings = {}
        # Attach a trace so that any change immediately updates the current profile and validates startup.
        self.modifier_var.trace_add("write", self.on_modifier_change)
        self.awaiting_rebind = False

        # Location settings
        self.x_var = SafeIntVar(value=0)
        self.y_var = SafeIntVar(value=0)
        self.use_location_var = tk.BooleanVar(value=False)

        # Random intervals settings
        self.random_intervals_var = tk.BooleanVar(value=False)
        self.random_interval_factor = ctk.StringVar(value="100")

        # Mouse still settings
        self.mouse_still_enabled = tk.BooleanVar(value=False)
        self.mouse_still_seconds = ctk.StringVar(value="1")
        self.last_mouse_pos = None
        self.last_mouse_move_time = time.perf_counter()

        # Stop at clicks settings
        self.stop_at_clicks_enabled = tk.BooleanVar(value=False)
        self.stop_at_clicks_value = ctk.StringVar(value="100")

        # Tracking for high CPS click loop.
        self.initial_start_time = None
        self.click_count = 0

        # Hotkey profiles – every profile stores all settings.
        self.current_profile = "Default"
        self.hotkey_profiles = {
            "Default": {
                "hotkey": self.hotkey,
                "modifier": self.modifier_var.get(),
                "hotkey_mode": self.hotkey_mode.get(),
                "cps": self.cps_var.get(),
                "x": self.x_var.get(),
                "y": self.y_var.get(),
                "use_location": self.use_location_var.get(),
                "random_intervals": self.random_intervals_var.get(),
                "random_interval_factor": self.random_interval_factor.get(),
                "mouse_still_enabled": self.mouse_still_enabled.get(),
                "mouse_still_seconds": self.mouse_still_seconds.get(),
                "stop_at_clicks_enabled": self.stop_at_clicks_enabled.get(),
                "stop_at_clicks_value": self.stop_at_clicks_value.get(),
                "activated": False
            }
        }
        # Startup tray option variable (global, not per-profile)
        self.minimize_to_tray_on_startup = tk.BooleanVar(value=False)

        # Load settings from file if available.
        self.load_settings()

        self.create_gui()
        self.update_profile_hotkey_bindings()
        self.running = True
        self.click_thread = threading.Thread(target=self.click_loop, daemon=True)
        self.click_thread.start()
        
        # Schedule periodic validation so that any changes in profiles update the startup option immediately.
        self.root.after(1000, self.periodic_validation)
        
        # Only if the app was launched with the --minimized flag (i.e. from Windows startup)
        # and the setting is enabled (and all profiles have valid modifier keys) do we minimize to tray.
        if ('--minimized' in sys.argv and 
            self.minimize_to_tray_on_startup.get() and 
            all(profile.get("modifier", "none") != "none" for profile in self.hotkey_profiles.values())):
            self.root.withdraw()
            self.root.after(100, self.minimize_to_tray)
            
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ------------------ Validation and Periodic Checks ------------------
    def on_modifier_change(self, *args):
        # Called whenever the active modifier is changed.
        self.hotkey_profiles[self.current_profile]["modifier"] = self.modifier_var.get()
        self.update_profile_hotkey_bindings()
        self.validate_startup_option(show_message=False)

    def validate_startup_option(self, show_message=False):
        """Checks all profiles and disables the startup option if any profile’s modifier is 'none'."""
        valid = all(profile.get("modifier", "none") != "none" for profile in self.hotkey_profiles.values())
        if not valid and self.minimize_to_tray_on_startup.get():
            self.minimize_to_tray_on_startup.set(False)
            self.remove_startup_registry_entry()
            if show_message:
                messagebox.showinfo("Info", "Minimize to tray on start up disabled because one or more profiles lack a modifier key.")

    def periodic_validation(self):
        """Periodically check all profiles to update the startup option state."""
        self.validate_startup_option(show_message=False)
        self.root.after(1000, self.periodic_validation)
    # ------------------ End Validation ------------------

    def load_settings(self):
        try:
            with open(self.settings_file, 'r') as f:
                data = json.load(f)
            if "hotkey_profiles" in data:
                self.hotkey_profiles = data["hotkey_profiles"]
            if "current_profile" in data:
                self.current_profile = data["current_profile"]
            self.minimize_to_tray_on_startup.set(data.get("minimize_to_tray_on_startup", False))
            current = self.hotkey_profiles.get(self.current_profile, {})
            self.hotkey = current.get("hotkey", self.hotkey)
            self.modifier_var.set(current.get("modifier", self.modifier_var.get()))
            self.hotkey_mode.set(current.get("hotkey_mode", self.hotkey_mode.get()))
            self.cps_var.set(current.get("cps", self.cps_var.get()))
            self.x_var.set(current.get("x", self.x_var.get()))
            self.y_var.set(current.get("y", self.y_var.get()))
            self.use_location_var.set(current.get("use_location", self.use_location_var.get()))
            self.random_intervals_var.set(current.get("random_intervals", self.random_intervals_var.get()))
            self.random_interval_factor.set(current.get("random_interval_factor", self.random_interval_factor.get()))
            self.mouse_still_enabled.set(current.get("mouse_still_enabled", self.mouse_still_enabled.get()))
            self.mouse_still_seconds.set(current.get("mouse_still_seconds", self.mouse_still_seconds.get()))
            self.stop_at_clicks_enabled.set(current.get("stop_at_clicks_enabled", self.stop_at_clicks_enabled.get()))
            self.stop_at_clicks_value.set(current.get("stop_at_clicks_value", self.stop_at_clicks_value.get()))
        except Exception:
            pass

    def save_settings(self):
        data = {
            "hotkey_profiles": self.hotkey_profiles,
            "current_profile": self.current_profile,
            "minimize_to_tray_on_startup": self.minimize_to_tray_on_startup.get()
        }
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"Error saving settings: {e}")

    def manual_save(self):
        self.save_current_profile()
        self.save_settings()
        messagebox.showinfo("Settings Saved", "Settings have been saved successfully.")

    def export_settings(self):
        downloads_path = os.path.join(os.path.expanduser("~"), "Downloads")
        filename = filedialog.asksaveasfilename(initialdir=downloads_path,
                                                defaultextension=".json",
                                                filetypes=[("JSON files", "*.json")],
                                                initialfile="settings_export.json",
                                                title="Export Settings")
        if filename:
            try:
                data = {
                    "hotkey_profiles": self.hotkey_profiles,
                    "current_profile": self.current_profile,
                    "minimize_to_tray_on_startup": self.minimize_to_tray_on_startup.get()
                }
                with open(filename, "w") as f:
                    json.dump(data, f, indent=4)
                messagebox.showinfo("Export Successful", f"Settings exported to {filename}")
            except Exception as e:
                messagebox.showerror("Export Failed", f"Error exporting settings: {e}")

    def import_settings(self):
        filename = filedialog.askopenfilename(defaultextension=".json",
                                              filetypes=[("JSON files", "*.json")],
                                              title="Load Settings")
        if filename:
            try:
                with open(filename, "r") as f:
                    data = json.load(f)
                if "hotkey_profiles" in data and "current_profile" in data:
                    self.hotkey_profiles = data["hotkey_profiles"]
                    self.current_profile = data["current_profile"]
                    self.minimize_to_tray_on_startup.set(data.get("minimize_to_tray_on_startup", False))
                    self.load_profile(self.current_profile)
                    messagebox.showinfo("Import Successful", f"Settings imported from {filename}")
                else:
                    messagebox.showerror("Import Failed", "The selected file does not contain valid settings data.")
            except Exception as e:
                messagebox.showerror("Import Failed", f"Error importing settings: {e}")

    def clamp_cps(self):
        try:
            value = int(self.cps_entry.get())
        except ValueError:
            value = 10
        value = max(1, min(value, 1000))
        self.cps_var.set(value)

    def clamp_random(self):
        try:
            value = int(self.random_interval_entry.get())
        except ValueError:
            value = 100
        value = max(10, min(value, 150))
        self.random_interval_factor.set(str(value))

    def clamp_mouse_still(self):
        try:
            value = int(self.mouse_still_entry.get())
        except ValueError:
            value = 1
        value = max(0, value)
        self.mouse_still_seconds.set(str(value))

    def clamp_stop_clicks(self):
        try:
            value = int(self.stop_at_clicks_entry.get())
        except ValueError:
            value = 1
        value = max(1, value)
        self.stop_at_clicks_value.set(str(value))

    def create_gui(self):
        main_frame = ctk.CTkFrame(self.root)
        main_frame.pack(fill="both", expand=True, padx=10, pady=10)
        main_frame.columnconfigure(0, weight=1, uniform="col")
        main_frame.columnconfigure(1, weight=1, uniform="col")

        # LEFT COLUMN: Hotkey & Toggle controls
        left_frame = ctk.CTkFrame(main_frame)
        left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 5), pady=5)
        self.toggle_button = ctk.CTkButton(left_frame, text="Start Auto Clicker", command=self.toggle_clicking)
        self.toggle_button.pack(pady=(10, 10), fill="x", padx=10)

        mode_frame = ctk.CTkFrame(left_frame)
        mode_frame.pack(pady=10, fill="x", padx=10)
        mode_label = ctk.CTkLabel(mode_frame, text="Hotkey Mode:")
        mode_label.pack(side="left", padx=(0, 5))
        self.hotkey_mode_menu = ctk.CTkOptionMenu(mode_frame,
                                                  values=["Click to Toggle", "Hold to Toggle"],
                                                  variable=self.hotkey_mode,
                                                  command=self.on_hotkey_mode_change)
        self.hotkey_mode_menu.pack(side="left", fill="x", expand=True)

        hotkey_frame = ctk.CTkFrame(left_frame, border_width=2, corner_radius=8)
        hotkey_frame.pack(pady=10, fill="x", padx=10)
        hotkey_title = ctk.CTkLabel(hotkey_frame, text="Hotkey Settings", font=ctk.CTkFont(size=16, weight="bold"))
        hotkey_title.pack(pady=(10, 5))

        key_frame = ctk.CTkFrame(hotkey_frame)
        key_frame.pack(pady=5, fill="x", padx=10)
        self.hotkey_label = ctk.CTkLabel(key_frame, text=f"Hotkey: {self.hotkey}")
        self.hotkey_label.pack(side="left", padx=(0, 5))
        self.change_hotkey_btn = ctk.CTkButton(key_frame, text="Change Hotkey", command=self.rebind_hotkey, width=120)
        self.change_hotkey_btn.pack(side="left", padx=(5, 0))

        modifier_frame = ctk.CTkFrame(hotkey_frame)
        modifier_frame.pack(pady=5, fill="x", padx=10)
        mod_label = ctk.CTkLabel(modifier_frame, text="Modifier Key:")
        mod_label.pack(side="left", padx=(0, 5))
        self.modifier_menu = ctk.CTkOptionMenu(modifier_frame,
                                               values=["none", "alt", "tab", "shift", "control"],
                                               variable=self.modifier_var)
        self.modifier_menu.pack(side="left", fill="x", expand=True)

        profiles_frame = ctk.CTkFrame(hotkey_frame, border_width=1, corner_radius=8)
        profiles_frame.pack(pady=10, fill="x", padx=10)
        profiles_header_frame = ctk.CTkFrame(profiles_frame)
        profiles_header_frame.pack(fill="x", padx=10, pady=(5, 0))
        profiles_label = ctk.CTkLabel(profiles_header_frame, text="Hotkey Profiles", font=ctk.CTkFont(size=14, weight="bold"))
        profiles_label.pack(side="left")
        add_profile_button = ctk.CTkButton(profiles_header_frame, text="+", width=30, command=self.add_profile)
        add_profile_button.pack(side="right")
        self.profiles_scroll_frame = ctk.CTkScrollableFrame(profiles_frame, height=120)
        self.profiles_scroll_frame.pack(pady=(5, 10), fill="x", padx=10)
        self.refresh_profiles_list()

        self.profile_indicator = ctk.CTkLabel(left_frame, text=f"Profile: {self.current_profile}", font=ctk.CTkFont(weight="bold"))
        self.profile_indicator.pack(pady=5, padx=10, fill="x")
        self.status_label = ctk.CTkLabel(left_frame, text="Status: Stopped")
        self.status_label.pack(pady=10, fill="x", padx=10)

        # RIGHT COLUMN: CPS and Click Settings
        right_frame = ctk.CTkFrame(main_frame)
        right_frame.grid(row=0, column=1, sticky="nsew", padx=(5, 0), pady=5)

        cps_frame = ctk.CTkFrame(right_frame, border_width=2, corner_radius=8)
        cps_frame.pack(pady=(10, 10), fill="x", padx=10)
        cps_title = ctk.CTkLabel(cps_frame, text="Clicks per Second (CPS)", font=ctk.CTkFont(size=16, weight="bold"))
        cps_title.pack(pady=(10, 5))
        self.cps_entry = ctk.CTkEntry(cps_frame, textvariable=self.cps_var, validate="key",
                                      validatecommand=(self.root.register(validate_numeric), "%P"))
        self.cps_entry.pack(pady=5, fill="x", padx=10)
        # When ENTER is pressed, move focus away so editing stops.
        self.cps_entry.bind("<Return>", lambda e: self.root.focus_set())
        self.cps_entry.bind("<FocusOut>", lambda e: self.clamp_cps())
        self.cps_slider = ctk.CTkSlider(cps_frame, from_=1, to=1000, variable=self.cps_var)
        self.cps_slider.pack(pady=5, fill="x", padx=10)

        location_frame = ctk.CTkFrame(right_frame, border_width=2, corner_radius=8)
        location_frame.pack(pady=(10, 10), fill="x", padx=10)
        loc_title = ctk.CTkLabel(location_frame, text="Click Settings", font=ctk.CTkFont(size=16, weight="bold"))
        loc_title.pack(pady=(10, 5))
        loc_label = ctk.CTkLabel(location_frame, text="Location (x, y):")
        loc_label.pack(anchor="w", padx=10)
        xy_frame = ctk.CTkFrame(location_frame)
        xy_frame.pack(pady=5, fill="x", padx=10)
        x_label = ctk.CTkLabel(xy_frame, text="X:")
        x_label.pack(side="left", padx=(0, 5))
        self.x_entry = ctk.CTkEntry(xy_frame, textvariable=self.x_var, width=70)
        self.x_entry.pack(side="left", padx=(0, 10))
        # Bind ENTER to remove focus from the X entry.
        self.x_entry.bind("<Return>", lambda e: self.root.focus_set())
        y_label = ctk.CTkLabel(xy_frame, text="Y:")
        y_label.pack(side="left", padx=(0, 5))
        self.y_entry = ctk.CTkEntry(xy_frame, textvariable=self.y_var, width=70)
        self.y_entry.pack(side="left", padx=(0, 10))
        # Bind ENTER to remove focus from the Y entry.
        self.y_entry.bind("<Return>", lambda e: self.root.focus_set())

        action_frame = ctk.CTkFrame(location_frame)
        action_frame.pack(pady=5, fill="x", padx=10)
        self.use_location_checkbox = ctk.CTkCheckBox(action_frame, text="Use Location", variable=self.use_location_var)
        self.use_location_checkbox.grid(row=0, column=0, sticky="w", padx=2, pady=2)
        self.set_location_btn = ctk.CTkButton(action_frame, text="Set Location (5s)", command=self.set_location, width=120)
        self.set_location_btn.grid(row=0, column=1, sticky="w", padx=2, pady=2)
        
        self.random_intervals_checkbox = ctk.CTkCheckBox(action_frame, text="Random Intervals", variable=self.random_intervals_var)
        self.random_intervals_checkbox.grid(row=1, column=0, sticky="w", padx=2, pady=2)
        random_frame_inner = ctk.CTkFrame(action_frame, fg_color="transparent")
        random_frame_inner.grid(row=1, column=1, sticky="w", padx=2, pady=2)
        self.random_interval_entry = ctk.CTkEntry(random_frame_inner, textvariable=self.random_interval_factor, width=60,
                                                  validate="key", validatecommand=(self.root.register(validate_numeric), "%P"))
        self.random_interval_entry.pack(side="left", padx=(0,0), pady=0)
        # Bind ENTER to remove focus from the random interval entry.
        self.random_interval_entry.bind("<Return>", lambda e: self.root.focus_set())
        self.random_interval_entry.bind("<FocusOut>", lambda e: self.clamp_random())
        percent_label = ctk.CTkLabel(random_frame_inner, text="%")
        percent_label.pack(side="left", padx=(2,0), pady=0)
        
        self.mouse_still_checkbox = ctk.CTkCheckBox(action_frame, text="Click only if mouse is still for", variable=self.mouse_still_enabled)
        self.mouse_still_checkbox.grid(row=2, column=0, sticky="w", padx=2, pady=2)
        mouse_frame_inner = ctk.CTkFrame(action_frame, fg_color="transparent")
        mouse_frame_inner.grid(row=2, column=1, sticky="w", padx=2, pady=2)
        self.mouse_still_entry = ctk.CTkEntry(mouse_frame_inner, textvariable=self.mouse_still_seconds, width=60,
                                              validate="key", validatecommand=(self.root.register(validate_numeric), "%P"))
        self.mouse_still_entry.pack(side="left", padx=(0,0), pady=0)
        # Bind ENTER to remove focus from the mouse still entry.
        self.mouse_still_entry.bind("<Return>", lambda e: self.root.focus_set())
        self.mouse_still_entry.bind("<FocusOut>", lambda e: self.clamp_mouse_still())
        seconds_label = ctk.CTkLabel(mouse_frame_inner, text="seconds")
        seconds_label.pack(side="left", padx=(2,0), pady=0)
        
        self.stop_at_clicks_checkbox = ctk.CTkCheckBox(action_frame, text="Stop at", variable=self.stop_at_clicks_enabled)
        self.stop_at_clicks_checkbox.grid(row=3, column=0, sticky="w", padx=2, pady=2)
        stop_frame_inner = ctk.CTkFrame(action_frame, fg_color="transparent")
        stop_frame_inner.grid(row=3, column=1, sticky="w", padx=2, pady=2)
        self.stop_at_clicks_entry = ctk.CTkEntry(stop_frame_inner, textvariable=self.stop_at_clicks_value, width=60,
                                                 validate="key", validatecommand=(self.root.register(validate_numeric), "%P"))
        self.stop_at_clicks_entry.pack(side="left", padx=(0,0), pady=0)
        # Bind ENTER to remove focus from the stop at clicks entry.
        self.stop_at_clicks_entry.bind("<Return>", lambda e: self.root.focus_set())
        self.stop_at_clicks_entry.bind("<FocusOut>", lambda e: self.clamp_stop_clicks())
        clicks_label = ctk.CTkLabel(stop_frame_inner, text="clicks")
        clicks_label.pack(side="left", padx=(2,0), pady=0)
        
        # Settings Management Buttons with no background box.
        settings_frame = ctk.CTkFrame(right_frame, fg_color="transparent", border_width=0)
        settings_frame.pack(pady=(5, 10), fill="x", padx=10)
        import_btn = ctk.CTkButton(settings_frame, text="Load Settings", command=self.import_settings)
        import_btn.pack(side="right", padx=5)
        export_btn = ctk.CTkButton(settings_frame, text="Export Settings", command=self.export_settings)
        export_btn.pack(side="right", padx=5)
        save_btn = ctk.CTkButton(settings_frame, text="Save Settings", command=self.manual_save)
        save_btn.pack(side="right", padx=5)
        # Tray-related frame.
        tray_frame = ctk.CTkFrame(right_frame, fg_color="transparent", border_width=0)
        tray_frame.pack(fill="x", padx=10, pady=(0,10))
        tray_btn = ctk.CTkButton(tray_frame, text="Minimize to Tray", command=self.minimize_to_tray)
        tray_btn.pack(pady=5, side="left")
        # Checkbox for "Minimize to tray on start up"
        self.startup_tray_checkbox = ctk.CTkCheckBox(tray_frame, text="Minimize to tray on start up", 
                                                     variable=self.minimize_to_tray_on_startup,
                                                     command=self.on_startup_tray_toggle)
        self.startup_tray_checkbox.pack(pady=5, side="left", padx=10)

    def on_hotkey_mode_change(self, mode):
        self.stop_clicking()
        if mode == "Hold to Toggle":
            self.toggle_button.configure(state="disabled")
            self.status_label.configure(text="Status: (Hold hotkey to click)")
        else:
            self.toggle_button.configure(state="normal")
            self.status_label.configure(text="Status: Stopped")
        self.update_profile_hotkey_bindings()

    def on_startup_tray_toggle(self):
        # When the startup checkbox is toggled by the user.
        if self.minimize_to_tray_on_startup.get():
            for profile in self.hotkey_profiles.values():
                if profile.get("modifier", "none") == "none":
                    messagebox.showerror("Error", "All profiles must have modifier keys")
                    self.minimize_to_tray_on_startup.set(False)
                    return
            self.add_startup_registry_entry()
        else:
            self.remove_startup_registry_entry()

    def add_startup_registry_entry(self):
        try:
            reg_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                     r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            if getattr(sys, 'frozen', False):
                exe_path = f'"{sys.executable}" --minimized'
            else:
                exe_path = f'"{sys.executable}" "{os.path.abspath(__file__)}" --minimized'
            winreg.SetValueEx(reg_key, "MultiPurposeAutoClicker", 0, winreg.REG_SZ, exe_path)
            winreg.CloseKey(reg_key)
        except Exception as e:
            print(f"Failed to add startup registry entry: {e}")

    def remove_startup_registry_entry(self):
        try:
            reg_key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                     r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE)
            winreg.DeleteValue(reg_key, "MultiPurposeAutoClicker")
            winreg.CloseKey(reg_key)
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"Failed to remove startup registry entry: {e}")

    def get_full_hotkey(self):
        modifier = self.modifier_var.get()
        return self.hotkey if modifier == "none" else f"{modifier}+{self.hotkey}"

    def get_profile_full_hotkey(self, profile):
        modifier = profile.get("modifier", "none")
        hotkey = profile.get("hotkey", "f8")
        return hotkey if modifier == "none" else f"{modifier}+{hotkey}"

    def update_profile_hotkey_bindings(self):
        # Remove any existing hotkey bindings.
        for binding_id in list(self.profile_hotkey_bindings.values()):
            try:
                keyboard.remove_hotkey(binding_id)
            except Exception:
                pass
        self.profile_hotkey_bindings.clear()
        for profile_name, profile in self.hotkey_profiles.items():
            if profile.get("activated", False):
                hotkey_combination = self.get_profile_full_hotkey(profile)
                binding_id = keyboard.add_hotkey(hotkey_combination,
                                                 lambda p=profile_name: self.on_profile_hotkey_pressed(p))
                self.profile_hotkey_bindings[profile_name] = binding_id

    def on_profile_hotkey_pressed(self, profile_name):
        self.save_current_profile()
        if self.current_profile != profile_name:
            self.load_profile(profile_name)
            self.status_label.configure(text=f"Switched to profile: {profile_name}")
            if not self.is_clicking and self.hotkey_mode.get() == "Click to Toggle":
                self.start_clicking()
        else:
            if self.hotkey_mode.get() == "Click to Toggle":
                self.toggle_clicking()

    def rebind_hotkey(self):
        try:
            keyboard.remove_hotkey(self.get_full_hotkey())
        except Exception:
            pass
        self.hotkey_label.configure(text="Press new hotkey...")
        self.awaiting_rebind = True
        threading.Thread(target=self.wait_for_new_hotkey, daemon=True).start()

    def wait_for_new_hotkey(self):
        new_key = keyboard.read_key(suppress=True)
        self.hotkey = new_key
        self.hotkey_label.configure(text=f"Hotkey: {self.hotkey}")
        self.awaiting_rebind = False
        self.hotkey_profiles[self.current_profile]["hotkey"] = self.hotkey
        self.update_profile_hotkey_bindings()

    def toggle_clicking(self):
        if self.awaiting_rebind:
            return
        if self.is_clicking:
            self.stop_clicking()
        else:
            self.start_clicking()

    def start_clicking(self):
        if self.is_clicking:
            return
        self.is_clicking = True
        self.toggle_button.configure(text="Stop Auto Clicker")
        self.status_label.configure(text="Status: Running")

    def stop_clicking(self):
        if not self.is_clicking:
            return
        self.is_clicking = False
        self.toggle_button.configure(text="Start Auto Clicker")
        self.status_label.configure(text="Status: Stopped")
        self.initial_start_time = None
        self.click_count = 0

    def set_location(self):
        threading.Thread(target=self._background_set_location, daemon=True).start()

    def _background_set_location(self):
        self.root.withdraw()
        time.sleep(5)
        x, y = get_mouse_position()
        self.root.deiconify()
        self.x_var.set(x)
        self.y_var.set(y)

    def check_stop_condition(self):
        if self.stop_at_clicks_enabled.get():
            try:
                limit = int(self.stop_at_clicks_value.get())
            except ValueError:
                limit = 1
            if self.click_count >= limit:
                self.stop_clicking()

    def click_loop(self):
        while self.running:
            if self.hotkey_mode.get() == "Click to Toggle":
                active = self.is_clicking
            else:
                active = keyboard.is_pressed(self.get_full_hotkey())
            if active:
                if self.mouse_still_enabled.get():
                    try:
                        threshold = float(self.mouse_still_seconds.get())
                    except ValueError:
                        threshold = 0
                    if threshold > 0:
                        pos1 = get_mouse_position()
                        time.sleep(0.005)
                        pos2 = get_mouse_position()
                        if pos1 != pos2:
                            self.last_mouse_move_time = time.perf_counter()
                            self.last_mouse_pos = pos2
                            time.sleep(0.01)
                            continue
                        if self.last_mouse_move_time is None:
                            self.last_mouse_move_time = time.perf_counter()
                        if time.perf_counter() - self.last_mouse_move_time < threshold:
                            time.sleep(0.01)
                            continue

                cps_value = max(self.cps_var.get(), 1)
                base_interval = 1.0 / cps_value
                if self.random_intervals_var.get():
                    if self.use_location_var.get():
                        set_cursor_position(self.x_var.get(), self.y_var.get())
                    send_left_click()
                    self.click_count += 1
                    self.check_stop_condition()
                    try:
                        factor = float(self.random_interval_factor.get())
                    except ValueError:
                        factor = 100.0
                    lower = base_interval * 0.1
                    upper = base_interval * (factor / 100.0)
                    sleep_interval = random.uniform(lower, upper)
                    time.sleep(sleep_interval)
                else:
                    current_time = time.perf_counter()
                    if self.initial_start_time is None:
                        self.initial_start_time = current_time
                        self.click_count = 0
                    elapsed_time = current_time - self.initial_start_time
                    expected_clicks = int(elapsed_time * cps_value)
                    if expected_clicks > self.click_count:
                        clicks_due = expected_clicks - self.click_count
                        for _ in range(clicks_due):
                            if self.use_location_var.get():
                                set_cursor_position(self.x_var.get(), self.y_var.get())
                            send_left_click()
                            self.click_count += 1
                            self.check_stop_condition()
                    time.sleep(base_interval)
            else:
                self.click_count = 0
                self.initial_start_time = None
                time.sleep(0.01)
        print("Click loop terminated.")

    def save_current_profile(self):
        self.hotkey_profiles[self.current_profile] = {
            "hotkey": self.hotkey,
            "modifier": self.modifier_var.get(),
            "hotkey_mode": self.hotkey_mode.get(),
            "cps": self.cps_var.get(),
            "x": self.x_var.get(),
            "y": self.y_var.get(),
            "use_location": self.use_location_var.get(),
            "random_intervals": self.random_intervals_var.get(),
            "random_interval_factor": self.random_interval_factor.get(),
            "mouse_still_enabled": self.mouse_still_enabled.get(),
            "mouse_still_seconds": self.mouse_still_seconds.get(),
            "stop_at_clicks_enabled": self.stop_at_clicks_enabled.get(),
            "stop_at_clicks_value": self.stop_at_clicks_value.get(),
            "activated": self.hotkey_profiles.get(self.current_profile, {}).get("activated", False)
        }
        self.update_profile_hotkey_bindings()

    def add_profile(self):
        profile_name = ask_profile_name(self.root, "Enter new profile name:")
        if profile_name:
            if profile_name in self.hotkey_profiles:
                messagebox.showerror("Error", "Profile already exists!")
                return
            self.save_current_profile()
            self.current_profile = profile_name
            self.hotkey_profiles[profile_name] = {
                "hotkey": self.hotkey,
                "modifier": self.modifier_var.get(),
                "hotkey_mode": self.hotkey_mode.get(),
                "cps": self.cps_var.get(),
                "x": self.x_var.get(),
                "y": self.y_var.get(),
                "use_location": self.use_location_var.get(),
                "random_intervals": self.random_intervals_var.get(),
                "random_interval_factor": self.random_interval_factor.get(),
                "mouse_still_enabled": self.mouse_still_enabled.get(),
                "mouse_still_seconds": self.mouse_still_seconds.get(),
                "stop_at_clicks_enabled": self.stop_at_clicks_enabled.get(),
                "stop_at_clicks_value": self.stop_at_clicks_value.get(),
                "activated": False
            }
            self.refresh_profiles_list()
            self.status_label.configure(text=f"Switched to profile: {profile_name}")
            self.profile_indicator.configure(text=f"Profile: {profile_name}")
            self.update_profile_hotkey_bindings()
            self.validate_startup_option(show_message=False)

    def refresh_profiles_list(self):
        for widget in self.profiles_scroll_frame.winfo_children():
            widget.destroy()
        for profile_name in self.hotkey_profiles:
            profile_frame = ctk.CTkFrame(self.profiles_scroll_frame)
            profile_frame.pack(fill="x", pady=2, padx=2)
            load_btn = ctk.CTkButton(profile_frame,
                                     text=profile_name,
                                     command=lambda name=profile_name: self.load_profile(name))
            if profile_name == self.current_profile:
                load_btn.configure(fg_color=("darkblue", "darkblue"))
            else:
                load_btn.configure(fg_color=("gray75", "gray25"))
            load_btn.pack(side="left", fill="x", expand=True)
            if profile_name != "Default":
                rename_btn = ctk.CTkButton(profile_frame, text="Rename", width=60,
                                           command=lambda name=profile_name: self.rename_profile(name))
                rename_btn.pack(side="left", padx=2)
                delete_btn = ctk.CTkButton(profile_frame, text="Delete", width=60,
                                           command=lambda name=profile_name: self.delete_profile(name))
                delete_btn.pack(side="left", padx=2)
            profile_data = self.hotkey_profiles[profile_name]
            activation_state = profile_data.get("activated", False)
            act_text = "Activated" if activation_state else "Deactivated"
            act_color = ("green", "green") if activation_state else ("red", "red")
            activation_btn = ctk.CTkButton(profile_frame, text=act_text, width=80,
                                           fg_color=act_color,
                                           command=lambda name=profile_name: self.toggle_activation(name))
            activation_btn.pack(side="left", padx=2)

    def load_profile(self, profile_name):
        self.save_current_profile()
        profile = self.hotkey_profiles.get(profile_name)
        if profile:
            self.current_profile = profile_name
            self.hotkey = profile.get("hotkey", "f8")
            self.hotkey_label.configure(text=f"Hotkey: {self.hotkey}")
            self.modifier_var.set(profile.get("modifier", "none"))
            self.hotkey_mode.set(profile.get("hotkey_mode", "Click to Toggle"))
            self.cps_var.set(profile.get("cps", 10))
            self.x_var.set(profile.get("x", 0))
            self.y_var.set(profile.get("y", 0))
            self.use_location_var.set(profile.get("use_location", False))
            self.random_intervals_var.set(profile.get("random_intervals", False))
            self.random_interval_factor.set(profile.get("random_interval_factor", "100"))
            self.mouse_still_enabled.set(profile.get("mouse_still_enabled", False))
            self.mouse_still_seconds.set(profile.get("mouse_still_seconds", "1"))
            self.stop_at_clicks_enabled.set(profile.get("stop_at_clicks_enabled", False))
            self.stop_at_clicks_value.set(profile.get("stop_at_clicks_value", "100"))
            self.profile_indicator.configure(text=f"Profile: {profile_name}")
            self.status_label.configure(text=f"Loaded profile: {profile_name}")
            self.refresh_profiles_list()
            self.update_profile_hotkey_bindings()
            self.validate_startup_option(show_message=False)
            
    def delete_profile(self, profile_name):
        if messagebox.askyesno("Delete Profile", f"Are you sure you want to delete the profile '{profile_name}'?"):
            if profile_name == self.current_profile:
                messagebox.showerror("Error", "Cannot delete the currently active profile!")
                return
            del self.hotkey_profiles[profile_name]
            self.refresh_profiles_list()
            self.status_label.configure(text=f"Deleted profile: {profile_name}")
            self.update_profile_hotkey_bindings()

    def rename_profile(self, profile_name):
        new_name = ask_profile_name(self.root, "Enter new profile name:", initial_value=profile_name)
        if new_name:
            if new_name in self.hotkey_profiles:
                messagebox.showerror("Error", "A profile with that name already exists!")
                return
            self.hotkey_profiles[new_name] = self.hotkey_profiles.pop(profile_name)
            if profile_name == self.current_profile:
                self.current_profile = new_name
                self.profile_indicator.configure(text=f"Profile: {new_name}")
            self.refresh_profiles_list()
            self.status_label.configure(text=f"Renamed profile '{profile_name}' to '{new_name}'")
            self.update_profile_hotkey_bindings()

    def toggle_activation(self, profile_name):
        profile = self.hotkey_profiles.get(profile_name, {})
        new_state = not profile.get("activated", False)
        if new_state:
            for other_name, other_profile in self.hotkey_profiles.items():
                if other_name != profile_name and other_profile.get("activated", False) and other_profile.get("hotkey") == profile.get("hotkey"):
                    other_profile["activated"] = False
        profile["activated"] = new_state
        self.refresh_profiles_list()
        self.status_label.configure(text=f"Profile '{profile_name}' activation set to {new_state}")
        self.update_profile_hotkey_bindings()

    def on_close(self):
        if messagebox.askyesno("Save Settings", "Do you want to save settings before closing?"):
            self.manual_save()
        self.running = False
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()

    # ------------------ Tray-related functions ------------------
    def minimize_to_tray(self):
        self.root.withdraw()
        try:
            image = Image.open(ICON_PATH)
        except Exception as e:
            print(f"Error loading tray icon: {e}")
            return

        menu = pystray.Menu(
            pystray.MenuItem("Show Full UI", self._tray_restore),
            pystray.MenuItem("Exit", self._tray_exit)
        )
        self.tray_icon = pystray.Icon("MultiPurposeAutoClicker", image, "Multi-Purpose Auto Clicker", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _tray_restore(self, icon, item):
        self.root.after(0, self.restore_from_tray)

    def restore_from_tray(self):
        self.root.deiconify()
        if self.tray_icon:
            self.tray_icon.stop()
            self.tray_icon = None

    def _tray_exit(self, icon, item):
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.after(0, self.on_close)
    # ------------------ End Tray-related ------------------

    def run(self):
        self.root.mainloop()

if __name__ == "__main__":
    app = MinimalAutoClicker()
    app.run()
