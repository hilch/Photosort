import argparse
import os
import hashlib
import queue
import re
import threading
from typing import Any, Callable, cast

import exifread
from geopy.geocoders import Nominatim

unbekannt = 0

geolocator = Nominatim(user_agent="geo_renamer")

def sanitize_filename(filename: str, max_length=255) -> str:
    """
    Converts a string into a valid Windows filename by removing restricted characters
    and truncating it if necessary.

    :param filename: The input filename string.
    :param max_length: Maximum length of the filename (default 255).
    :return: A sanitized filename.
    """
    # Define invalid characters for Windows filenames
    invalid_chars = r'[<>:"/\\|?*]'
    filename = re.sub(invalid_chars, '_', filename)  # Replace invalid characters with '_'

    # Strip leading and trailing spaces and periods (Windows restrictions)
    filename = filename.strip(' .')

    # Ensure filename isn't excessively long
    if len(filename) > max_length:
        filename = filename[:max_length]

    return filename


def compute_sha256(file_path):
    """Compute the SHA-256 hash of a file."""
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for byte_block in iter(lambda: f.read(4096), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()


def get_exif_data(image_path):
    global unbekannt

    with open(image_path, 'rb') as f:
        tags = exifread.process_file(f)
        date_taken = tags.get('EXIF DateTimeOriginal', None)
        gps_latitude = tags.get('GPS GPSLatitude', None)
        gps_longitude = tags.get('GPS GPSLongitude', None)
        
        if date_taken:
            date_taken = str(date_taken).replace(':', '-')
        
        if gps_latitude and gps_longitude:
            lat = convert_to_degrees(gps_latitude)
            lon = convert_to_degrees(gps_longitude)
            location = get_location(lat, lon)
        else:
            unbekannt = unbekannt + 1
            location = f" {unbekannt}"

        return date_taken, location

def convert_to_degrees(value):
    """convert GPS coordinates to degrees"""
    d, m, s = value.values
    return d + (m / 60.0) + (s / 3600.0)

def get_location(lat, lon):
    global unbekannt

    """converts GPS coordinates to locations by help of geopy"""
    try:
        location = geolocator.reverse((lat, lon), exactly_one=True)
        if location:
            raw_location = cast(dict[str, Any], cast(Any, location).raw)
            address = raw_location.get("address", {})

            country_code = str(address.get('country_code', '')).upper()
            ret = country_code + '-'
            city = ''
            if 'city' in address:
                city = address['city']
            elif 'municipality' in address:
                city = address['municipality']
            elif 'town' in address:
                city = address["town"]
            elif 'village' in address:
                city = address['village']
            else:
                pass
            ret += city
            road = ''
            if 'road' in address:
                road = address['road']
                ret = ret + ' ' + road
            if 'country' in address and ret == '':
                ret += address['country']
            return ret
        else:
            unbekannt = unbekannt + 1
            return str(unbekannt)
    except BaseException as e:
        unbekannt = unbekannt + 1
        return str(unbekannt)

def rename_images(
    directory: str,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> None:
    image_filenames = [
        filename
        for filename in os.listdir(directory)
        if filename.lower().endswith((".jpg", ".jpeg", ".png"))
    ]
    hashes = set()
    total = len(image_filenames)

    for completed, filename in enumerate(image_filenames, start=1):
        image_path = os.path.join(directory, filename)
        if progress_callback:
            progress_callback(filename, completed - 1, total)

        file_hash = compute_sha256(image_path)
        if file_hash in hashes:
            os.remove(image_path)
        else:
            hashes.add(file_hash)
            date_taken, location = get_exif_data(image_path)
            if date_taken:
                new_filename = sanitize_filename(f"{date_taken} {location}.jpg")
                new_path = os.path.join(directory, new_filename)
                try:
                    os.rename(image_path, new_path)
                except FileExistsError:
                    pass
                print(f"Renamed: {filename} -> {new_filename}")

        if progress_callback:
            progress_callback(filename, completed, total)


def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Photo Sort")
    root.resizable(False, False)

    directory_var = tk.StringVar()
    current_file_var = tk.StringVar(value="No file processed")
    events: queue.Queue[tuple[str, str, int, int]] = queue.Queue()

    def select_directory() -> None:
        directory = filedialog.askdirectory(title="Select photo folder")
        if directory:
            directory_var.set(directory)

    def start_conversion() -> None:
        directory = directory_var.get()
        if not os.path.isdir(directory):
            messagebox.showerror("Invalid folder", "Select an existing folder first.")
            return

        start_button.configure(state="disabled")
        progress.configure(maximum=1, value=0)
        current_file_var.set("Starting...")

        def worker() -> None:
            try:
                rename_images(
                    directory,
                    lambda filename, completed, total: events.put(
                        ("progress", filename, completed, total)
                    ),
                )
            except Exception as error:
                events.put(("error", str(error), 0, 0))
            else:
                events.put(("done", "", 0, 0))

        threading.Thread(target=worker, daemon=True).start()

    def poll_events() -> None:
        try:
            while True:
                event, value, completed, total = events.get_nowait()
                if event == "progress":
                    current_file_var.set(value)
                    progress.configure(maximum=max(total, 1), value=completed)
                elif event == "error":
                    start_button.configure(state="normal")
                    messagebox.showerror("Conversion failed", value)
                else:
                    start_button.configure(state="normal")
                    current_file_var.set("Finished")
        except queue.Empty:
            pass
        root.after(100, poll_events)

    folder_frame = ttk.Frame(root, padding=12)
    folder_frame.grid(row=0, column=0, sticky="ew")
    folder_frame.columnconfigure(0, weight=1)
    ttk.Entry(folder_frame, textvariable=directory_var, width=48).grid(
        row=0, column=0, padx=(0, 8), sticky="ew"
    )
    ttk.Button(folder_frame, text="Select path", command=select_directory).grid(
        row=0, column=1
    )

    ttk.Label(root, text="Currently processed file:").grid(
        row=1, column=0, padx=12, sticky="w"
    )
    ttk.Entry(root, textvariable=current_file_var, state="readonly", width=64).grid(
        row=2, column=0, padx=12, pady=(4, 10), sticky="ew"
    )
    progress = ttk.Progressbar(root, mode="determinate", length=440)
    progress.grid(row=3, column=0, padx=12, sticky="ew")
    start_button = ttk.Button(root, text="Start conversion", command=start_conversion)
    start_button.grid(row=4, column=0, padx=12, pady=12, sticky="e")

    root.after(100, poll_events)
    root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sort and rename photos in a folder.")
    parser.add_argument(
        "directory",
        nargs="?",
        help="photo folder to process; omit to open the graphical interface",
    )
    args = parser.parse_args()

    if args.directory:
        if not os.path.isdir(args.directory):
            parser.error(f"not an existing directory: {args.directory}")
        rename_images(args.directory)
    else:
        run_gui()


if __name__ == "__main__":
    main()





