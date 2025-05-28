import os
import hashlib
import re
import exifread
from geopy.geocoders import Nominatim

unbekannt = 0

geolocator = Nominatim(user_agent="geo_renamer", timeout=5)

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
            address = location.raw["address"]

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

def rename_images(directory):
    hashes = []

    for filename in os.listdir(directory):
        if filename.lower().endswith(('.jpg', '.jpeg', '.png')):
            image_path = os.path.join(directory, filename)

            # Dupletten entfernen
            hash = compute_sha256(image_path)
            if hash not in hashes:
                hashes.append(hash)
            else:
                os.remove(image_path)
                continue

            date_taken, location = get_exif_data(image_path)
            if date_taken:
                new_filename = sanitize_filename( f"{date_taken} {location}.jpg" )
                new_path = os.path.join(directory, new_filename)
                try:
                    os.rename(image_path, new_path)
                except FileExistsError:
                    pass
                print(f"Renamed: {filename} -> {new_filename}")

# Start
rename_images(r"<path to folder with photos>")





