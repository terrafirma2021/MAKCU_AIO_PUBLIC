import requests
import threading
import os
import json
import aiohttp
import asyncio
import time
from tenacity import retry, stop_after_attempt, wait_fixed
from aiohttp import ContentTypeError
from ping3 import ping
from modules.utils import get_main_folder, get_download_path

class ConfigManager:
    """
    Handles downloading, storing, and accessing configuration data and firmware BIN files.
    Tracks online/offline state and selects the fastest server (GitHub or Gitee) based on ping latency.
    Downloads config.json and BIN files in the background on initialization.
    """
    PRIMARY_CONFIG_URL = "https://raw.githubusercontent.com/terrafirma2021/MAKCM_v2_files/main/config.json"
    FALLBACK_CONFIG_URL = "https://gitee.com/terrafirma/MAKCM_v2_files/raw/main/config.json"
    LOCAL_CONFIG_PATH = os.path.join(get_main_folder(), 'config.json')
    PING_TIMEOUT = 0.5  # 500ms timeout for pings

    def __init__(self, logger, progress_callback=None):
        self.logger = logger
        self.progress_callback = progress_callback
        self.config_data = {}
        self.config_lock = threading.Lock()
        self.download_complete = threading.Event()
        self.download_successful = False
        self.is_online = False
        self.bin_file_urls = {}
        self.side_to_filename = {}
        self.bin_files_downloaded = {}
        # Select the fastest server based on ping
        self.preferred_server = self._select_fastest_server()
        # Load local config immediately
        self.load_local_config()
        self._parse_firmware_info()
        # Start background download of config and BIN files
        threading.Thread(target=self.download_all_files, daemon=True).start()
        
        
    @staticmethod
    def _parse_ping(raw) -> float | None:
        """
        Normalize ping3 results to seconds (float), or return None on failure.
        Accepts: 0.002, "2ms", "4.1ms", "0.003"
        """
        if raw is None:
            return None

        if isinstance(raw, (int, float)):
            return float(raw)

        if isinstance(raw, str):
            s = raw.strip().lower()
            factor = 1.0
            if s.endswith("ms"):
                s = s[:-2].strip()
                factor = 1 / 1000
            elif s.endswith("s"):
                s = s[:-1].strip()

            try:
                return float(s) * factor
            except ValueError:
                return None

        return None


    def _select_fastest_server(self):
        """Ping GitHub and Gitee concurrently and return the host with the lowest latency."""
        servers = [
            ("GitHub", "raw.githubusercontent.com"),
            ("Gitee", "gitee.com")
        ]
        results = {}
    
        def safe_ping(name, host):
            try:
                latency = self._parse_ping(ping(host, timeout=self.PING_TIMEOUT))
                results[name] = latency if latency is not None else -1.0
            except Exception:
                results[name] = -1.0
    
        threads = [threading.Thread(target=safe_ping, args=(name, host), daemon=True) for name, host in servers]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    
        valid = {k: v for k, v in results.items() if v >= 0}
        fastest = min(valid, key=valid.get) if valid else "GitHub"
    
        def fmt(name):
            latency = results.get(name, -1)
            return f"{latency * 1000:.2f}ms" if latency >= 0 else "failed"
    
        github_ping = fmt("GitHub")
        gitee_ping = fmt("Gitee")
    
        self.logger.terminal_print(f"Connected to GitHub: {github_ping}  Gitee: {gitee_ping}")
        return fastest
    




    def load_local_config(self):
        """Load the local config.json if it exists."""
        if os.path.exists(self.LOCAL_CONFIG_PATH):
            try:
                with open(self.LOCAL_CONFIG_PATH, 'r', encoding="utf-8") as f:
                    with self.config_lock:
                        self.config_data = json.load(f)
                        self.download_successful = True
                        self.is_online = self.config_data.get("is_online", False)
            except Exception:
                pass

    def _parse_firmware_info(self):
        """Build dictionaries of firmware filenames and URLs from config data."""
        firmware = self.config_data.get("firmware", {})
        self.bin_file_urls = {}
        self.side_to_filename = {}
        self.bin_files_downloaded = {}
        for side, info in firmware.items():
            name = info.get("name")
            primary_url = info.get("primary_url")
            fallback_url = info.get("fallback_url")
            if not name or not primary_url or not fallback_url:
                continue
            filename = f"{name}.bin" if not name.endswith('.bin') else name
            self.bin_file_urls[filename] = {
                "primary": primary_url,
                "fallback": fallback_url,
            }
            self.side_to_filename[side] = filename
            self.bin_files_downloaded[filename] = self._is_valid_file(get_download_path(filename))

    def _is_valid_file(self, filepath):
        """Check if a file exists and is non-empty."""
        return os.path.exists(filepath) and os.path.getsize(filepath) > 0

    async def download_file_async(self, session, filename, primary_url, fallback_url):
        """Download a single file with retries, trying primary then fallback URL."""
        bin_path = get_download_path(filename)
        os.makedirs(os.path.dirname(bin_path), exist_ok=True)

        # Skip download if file exists and is valid
        if self._is_valid_file(bin_path):
            if self.progress_callback:
                self.progress_callback(filename, "skipped")
            return self.config_data.get("last_successful_server", "github")

        @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
        async def fetch(url):
            async with session.get(url, timeout=10) as response:
                response.raise_for_status()
                with open(bin_path, 'wb') as f:
                    async for chunk in response.content.iter_chunked(8192):
                        f.write(chunk)
            return True

        # Use preferred server first
        urls = [(primary_url, "GitHub"), (fallback_url, "Gitee")]
        if self.preferred_server == "Gitee":
            urls = [(fallback_url, "Gitee"), (primary_url, "GitHub")]

        for url, server in urls:
            try:
                await fetch(url)
                if self.progress_callback:
                    self.progress_callback(filename, "success")
                return server.lower()
            except Exception:
                if self.progress_callback:
                    self.progress_callback(filename, "failed")
        return None

    async def download_all_files_async(self):
        """Download config.json and all BIN files asynchronously."""
        start_time = time.time()
        if self.progress_callback:
            self.progress_callback("all", "starting")  # Log "Downloading files..."
        async with aiohttp.ClientSession() as session:
            # Download config
            @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
            async def fetch_config(url):
                async with session.get(url, timeout=10) as response:
                    response.raise_for_status()
                    try:
                        content_type = response.content_type
                        if content_type not in ['application/json', 'text/plain']:
                            raise ContentTypeError(f"Expected JSON or text/plain, got {content_type} from {url}")
                    except TypeError:
                        raise
                    try:
                        content = await response.text(encoding='utf-8')
                    except TypeError:
                        raise
                    try:
                        data = json.loads(content)
                    except json.JSONDecodeError:
                        raise
                    except TypeError:
                        raise
                    return data

            # Try preferred server first for config
            config_urls = [
                (self.PRIMARY_CONFIG_URL, "GitHub"),
                (self.FALLBACK_CONFIG_URL, "Gitee")
            ]
            if self.preferred_server == "Gitee":
                config_urls = [
                    (self.FALLBACK_CONFIG_URL, "Gitee"),
                    (self.PRIMARY_CONFIG_URL, "GitHub")
                ]

            for config_url, server in config_urls:
                try:
                    with self.config_lock:
                        current_window_position = self.config_data.get("window_position", None)
                        self.config_data = await fetch_config(config_url)
                        self.download_successful = True
                        self.is_online = True
                        self.config_data["is_online"] = True
                        self.config_data["last_successful_server"] = server.lower()
                        if current_window_position:
                            self.config_data["window_position"] = current_window_position
                    self._parse_firmware_info()
                    self.save_config_to_file()
                    if self.progress_callback:
                        self.progress_callback("config.json", "success")
                    break  # Exit loop on success
                except (ContentTypeError, json.JSONDecodeError, TypeError, Exception):
                    pass
            else:
                # Both servers failed
                with self.config_lock:
                    self.is_online = False
                    self.config_data["is_online"] = False
                self._parse_firmware_info()
                self.save_config_to_file()
                if self.progress_callback:
                    self.progress_callback("config.json", "failed")

            # Download BIN files
            tasks = []
            for filename, urls in self.bin_file_urls.items():
                tasks.append(self.download_file_async(session, filename, urls["primary"], urls["fallback"]))

            results = await asyncio.gather(*tasks, return_exceptions=True)
            with self.config_lock:
                for filename, result in zip(self.bin_file_urls.keys(), results):
                    if isinstance(result, str):
                        self.bin_files_downloaded[filename] = True
                        self.config_data["last_successful_server"] = result
                    else:
                        self.bin_files_downloaded[filename] = False
                self.is_online = any(self.bin_files_downloaded.values()) or self.download_successful
                self.config_data["is_online"] = self.is_online
                self.save_config_to_file()

        # Log completion
        elapsed_time = time.time() - start_time
        if self.progress_callback:
            self.progress_callback("all", "complete")  # Log "Download complete"

    def download_all_files(self):
        """Run the async download in a thread."""
        asyncio.run(self.download_all_files_async())
        self.download_complete.set()

    def save_config_to_file(self):
        """Save config_data to local config.json."""
        try:
            config_dir = os.path.dirname(self.LOCAL_CONFIG_PATH)
            os.makedirs(config_dir, exist_ok=True)
            if not os.access(config_dir, os.W_OK):
                raise PermissionError(f"No write permission for {config_dir}")
            with open(self.LOCAL_CONFIG_PATH, 'w', encoding="utf-8") as f:
                json.dump(self.config_data, f, indent=4)
        except Exception:
            pass

    def get_config_value(self, key, default=None):
        """Retrieve a value from config_data."""
        with self.config_lock:
            return self.config_data.get(key, default)

    def set_config_value(self, key, value):
        """Set a value in config_data and save to file."""
        with self.config_lock:
            self.config_data[key] = value
            self.save_config_to_file()

    def wait_until_downloaded(self, timeout=30):
        """Block until all downloads complete."""
        return self.download_complete.wait(timeout)

    def is_online_status(self):
        """Return current online/offline status."""
        with self.config_lock:
            return self.is_online

    def get_firmware_info(self, side):
        """Return firmware info dict for a given side."""
        filename = self.side_to_filename.get(side)
        if not filename:
            return None
        urls = self.bin_file_urls.get(filename, {})
        name = filename[:-4] if filename.endswith('.bin') else filename
        return {
            "name": name,
            "filename": filename,
            "primary_url": urls.get("primary"),
            "fallback_url": urls.get("fallback"),
        }

    def get_firmware_urls(self, side):
        """Return primary and fallback URLs for a given side."""
        info = self.get_firmware_info(side)
        if not info:
            return None, None
        return info["primary_url"], info["fallback_url"]

    def is_bin_downloaded(self, name):
        """Check if a BIN file (by base name) has been downloaded."""
        filename = f"{name}.bin" if not name.endswith('.bin') else name
        bin_path = get_download_path(filename)
        return self._is_valid_file(bin_path) and self.bin_files_downloaded.get(filename, False)