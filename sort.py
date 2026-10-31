import argparse
import os
import hashlib
import queue
import re
import threading
from typing import Any, Callable, Literal, cast

import exifread
from geopy.extra.rate_limiter import RateLimiter
from geopy.geocoders import Nominatim

unbekannt = 0
GeolocationAction = Literal["retry", "cancel", "abort"]

geolocator = Nominatim(user_agent="geo_renamer")
reverse_geocode = RateLimiter(
    geolocator.reverse,
    min_delay_seconds=1,
    max_retries=2,
    error_wait_seconds=5,
    swallow_exceptions=False,
)


def report_geolocation_failure(
    message: str,
    warning_callback: Callable[[str], None] | None = None,
) -> None:
    if warning_callback:
        warning_callback(message)
    else:
        print(f"Warning: {message}")


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


def get_exif_data(
    image_path,
    timeout=20,
    warning_callback: Callable[[str], None] | None = None,
    retry_callback: Callable[[str], GeolocationAction] | None = None,
):
    global unbekannt
    last_action: GeolocationAction | None = None

    while True:
        with open(image_path, 'rb') as f:
            tags = exifread.process_file(f)
            date_taken = tags.get('EXIF DateTimeOriginal', None)
            gps_latitude = tags.get('GPS GPSLatitude', None)
            gps_longitude = tags.get('GPS GPSLongitude', None)

        if date_taken:
            date_taken = str(date_taken).replace(':', '-')

        if gps_latitude and gps_longitude:
            break
        failure = f"{os.path.basename(image_path)}: GPS coordinates are missing."
        report_geolocation_failure(failure, warning_callback)
        action = retry_callback(failure) if retry_callback else "cancel"
        last_action = action
        if action == "retry":
            continue
        break

    if gps_latitude and gps_longitude:
        lat = convert_to_degrees(gps_latitude)
        lon = convert_to_degrees(gps_longitude)

        def format_failure(reason: str) -> str:
            filename = os.path.basename(image_path)
            return f"{filename}: {reason}"

        def request_action(reason: str) -> GeolocationAction:
            nonlocal last_action
            last_action = (
                retry_callback(format_failure(reason))
                if retry_callback
                else "cancel"
            )
            return last_action

        location = get_location(
            lat,
            lon,
            timeout,
            lambda message: report_geolocation_failure(
                format_failure(message), warning_callback
            ),
            request_action,
        )
        if retry_callback and last_action == "cancel":
            location = ""
    else:
        unbekannt = unbekannt + 1
        location = (
            ""
            if retry_callback and last_action == "cancel"
            else f" {unbekannt}"
        )

    return date_taken, location

def convert_to_degrees(value):
    """convert GPS coordinates to degrees"""
    d, m, s = value.values
    return d + (m / 60.0) + (s / 3600.0)

def get_location(
    lat,
    lon,
    timeout=20,
    warning_callback: Callable[[str], None] | None = None,
    retry_callback: Callable[[str], GeolocationAction] | None = None,
):
    global unbekannt

    """converts GPS coordinates to locations by help of geopy"""
    while True:
        try:
            location = reverse_geocode(
                (lat, lon), exactly_one=True, timeout=timeout
            )
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
                ret += city
                if 'road' in address:
                    ret += ' ' + address['road']
                if 'country' in address and ret == '':
                    ret += address['country']
                return ret
            failure = f"No result for coordinates ({lat}, {lon})."
        except Exception as error:
            failure = f"Lookup failed for coordinates ({lat}, {lon}): {error}"

        report_geolocation_failure(failure, warning_callback)
        action = retry_callback(failure) if retry_callback else "cancel"
        if action == "retry":
            continue

        unbekannt = unbekannt + 1
        return str(unbekannt)

def rename_images(
    directory: str,
    progress_callback: Callable[[str, int, int], None] | None = None,
    timeout: int = 20,
    warning_callback: Callable[[str], None] | None = None,
    retry_callback: Callable[[str], GeolocationAction] | None = None,
    abort_callback: Callable[[], bool] | None = None,
) -> None:
    image_filenames = [
        filename
        for filename in os.listdir(directory)
        if filename.lower().endswith((".jpg", ".jpeg", ".png"))
    ]
    hashes = set()
    total = len(image_filenames)

    for completed, filename in enumerate(image_filenames, start=1):
        if abort_callback and abort_callback():
            break

        image_path = os.path.join(directory, filename)
        if progress_callback:
            progress_callback(filename, completed - 1, total)

        file_hash = compute_sha256(image_path)
        if abort_callback and abort_callback():
            break

        if file_hash in hashes:
            os.remove(image_path)
        else:
            hashes.add(file_hash)
            date_taken, location = get_exif_data(
                image_path,
                timeout,
                warning_callback,
                retry_callback,
            )
            if abort_callback and abort_callback():
                break

            if date_taken:
                filename_parts = [date_taken]
                if location.strip():
                    filename_parts.append(location.strip())
                new_filename = sanitize_filename(f"{' '.join(filename_parts)}.jpg")
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
    timeout_var = tk.IntVar(value=20)
    warning_var = tk.StringVar(value="Geolocation warnings: 0")
    location_warning_count = 0
    latest_location_warning = ""
    abort_requested = threading.Event()
    events: queue.Queue[tuple[str, str, int, int]] = queue.Queue()
    retry_requests: queue.Queue[
        tuple[str, queue.Queue[GeolocationAction]]
    ] = queue.Queue()

    def ask_geolocation_action(message: str) -> GeolocationAction:
        dialog = tk.Toplevel(root)
        dialog.title("Geolocation failed")
        dialog.transient(root)
        dialog.resizable(False, False)
        result: list[GeolocationAction] = []

        def finish(action: GeolocationAction) -> None:
            result.append(action)
            dialog.grab_release()
            dialog.destroy()

        ttk.Label(dialog, text=message, wraplength=460, justify="left").grid(
            row=0, column=0, columnspan=3, padx=16, pady=16, sticky="w"
        )
        ttk.Button(dialog, text="Retry", command=lambda: finish("retry")).grid(
            row=1, column=0, padx=(16, 8), pady=(0, 12), sticky="ew"
        )
        ttk.Button(dialog, text="Skip", command=lambda: finish("cancel")).grid(
            row=1, column=1, padx=8, pady=(0, 12), sticky="ew"
        )
        ttk.Button(dialog, text="Abort", command=lambda: finish("abort")).grid(
            row=1, column=2, padx=(8, 16), pady=(0, 12), sticky="ew"
        )
        for column in range(3):
            dialog.columnconfigure(column, weight=1)
        dialog.protocol("WM_DELETE_WINDOW", lambda: finish("cancel"))
        dialog.bind("<Escape>", lambda event: finish("cancel"))
        dialog.grab_set()
        dialog.wait_window()
        return result[0] if result else "cancel"

    def select_directory() -> None:
        directory = filedialog.askdirectory(title="Select photo folder")
        if directory:
            directory_var.set(directory)

    def abort_conversion() -> None:
        abort_requested.set()
        abort_button.configure(state="disabled")
        current_file_var.set("Aborting...")

    def start_conversion() -> None:
        nonlocal location_warning_count, latest_location_warning

        directory = directory_var.get()
        if not os.path.isdir(directory):
            messagebox.showerror("Invalid folder", "Select an existing folder first.")
            return

        try:
            timeout = timeout_var.get()
            if timeout < 1:
                raise ValueError
        except (tk.TclError, ValueError):
            messagebox.showerror("Invalid timeout", "Enter at least 1 second.")
            return

        start_button.configure(state="disabled")
        abort_button.configure(state="normal")
        progress.configure(maximum=1, value=0)
        current_file_var.set("Starting...")
        location_warning_count = 0
        latest_location_warning = ""
        warning_var.set("Geolocation warnings: 0")
        abort_requested.clear()

        def worker() -> None:
            try:
                def request_retry(message: str) -> GeolocationAction:
                    response: queue.Queue[GeolocationAction] = queue.Queue(maxsize=1)
                    retry_requests.put((message, response))
                    return response.get()

                rename_images(
                    directory,
                    lambda filename, completed, total: events.put(
                        ("progress", filename, completed, total)
                    ),
                    timeout,
                    lambda message: events.put(("warning", message, 0, 0)),
                    request_retry,
                    abort_requested.is_set,
                )
            except Exception as error:
                events.put(("error", str(error), 0, 0))
            else:
                events.put(("done", "", 0, 0))

        threading.Thread(target=worker, daemon=True).start()

    def poll_events() -> None:
        nonlocal location_warning_count, latest_location_warning

        try:
            while True:
                event, value, completed, total = events.get_nowait()
                if event == "progress":
                    current_file_var.set(value)
                    progress.configure(maximum=max(total, 1), value=completed)
                elif event == "warning":
                    location_warning_count += 1
                    latest_location_warning = value
                    warning_var.set(
                        f"Geolocation warnings: {location_warning_count}. Latest: {value}"
                    )
                elif event == "error":
                    start_button.configure(state="normal")
                    abort_button.configure(state="disabled")
                    messagebox.showerror("Conversion failed", value)
                else:
                    start_button.configure(state="normal")
                    abort_button.configure(state="disabled")
                    current_file_var.set(
                        "Aborted" if abort_requested.is_set() else "Finished"
                    )
                    if location_warning_count:
                        messagebox.showwarning(
                            "Geolocation warnings",
                            f"Encountered {location_warning_count} geolocation "
                            f"warning(s) during processing.\n"
                            f"Latest warning: {latest_location_warning}",
                        )
        except queue.Empty:
            pass

        try:
            while True:
                message, response = retry_requests.get_nowait()
                action = ask_geolocation_action(message)
                if action == "abort":
                    abort_requested.set()
                response.put(action)
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

    ttk.Label(root, text="Geocoding timeout (seconds):").grid(
        row=1, column=0, padx=12, pady=(0, 8), sticky="w"
    )
    ttk.Spinbox(root, from_=1, to=3600, textvariable=timeout_var, width=8).grid(
        row=1, column=0, padx=190, pady=(0, 8), sticky="w"
    )

    ttk.Label(root, text="Currently processed file:").grid(
        row=2, column=0, padx=12, sticky="w"
    )
    ttk.Entry(root, textvariable=current_file_var, state="readonly", width=64).grid(
        row=3, column=0, padx=12, pady=(4, 10), sticky="ew"
    )
    ttk.Label(root, textvariable=warning_var, wraplength=440).grid(
        row=4, column=0, padx=12, sticky="w"
    )
    progress = ttk.Progressbar(root, mode="determinate", length=440)
    progress.grid(row=5, column=0, padx=12, sticky="ew")
    actions_frame = ttk.Frame(root, padding=(12, 12, 12, 0))
    actions_frame.grid(row=6, column=0, sticky="ew")
    actions_frame.columnconfigure(0, weight=1)
    abort_button = ttk.Button(
        actions_frame, text="Abort", command=abort_conversion, state="disabled"
    )
    abort_button.grid(row=0, column=0, sticky="w")
    start_button = ttk.Button(
        actions_frame, text="Start conversion", command=start_conversion
    )
    start_button.grid(row=0, column=1, sticky="e")

    root.after(100, poll_events)
    root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Sort and rename photos in a folder.")
    parser.add_argument(
        "directory",
        nargs="?",
        help="photo folder to process; omit to open the graphical interface",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20,
        help="geocoding timeout in seconds (default: 20)",
    )
    args = parser.parse_args()

    if args.timeout < 1:
        parser.error("--timeout must be at least 1 second")

    if args.directory:
        if not os.path.isdir(args.directory):
            parser.error(f"not an existing directory: {args.directory}")
        rename_images(args.directory, timeout=args.timeout)
    else:
        run_gui()


if __name__ == "__main__":
    main()





