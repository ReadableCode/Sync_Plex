# %%
## Imports ##


if __name__ != "__main__":
    print(f"Importing {__name__}")

import time
import requests
import xml.etree.ElementTree as ET
import os
import sys
from dotenv import load_dotenv
import shutil
import subprocess
import pandas as pd

# append grandparent
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.config_utils import (
    file_dir,
    parent_dir,
    grandparent_dir,
    great_grandparent_dir,
    data_dir,
)

from utils.display_tools import pprint_dict, pprint_ls, print_logger, pprint_df


# %%
# ADB Setup #


# On Windows choco can be used to install ADP by openning a powershell window as admin and running  the command:
# choco install adb


# %%
## Variables ##


# Try to find adb in the system PATH
adb_path = shutil.which("adb")

# If adb is not found in the PATH, use the hardcoded path
if not adb_path:
    print_logger("adb not found in PATH, using hardcoded path")
    adb_path = r"E:\OneDrive\Desktop\platform-tools_r34.0.5-windows\platform-tools\adb"
else:
    print_logger("adb found in PATH")


# %%
## Functions ##


def ls_directory(adb_folder_path):
    command = [adb_path, "shell", "ls", adb_folder_path]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=True, shell=True
        )
        return result.stdout.splitlines()
    except subprocess.CalledProcessError as e:
        print(f"Error listing directory: {e}\ncommand was:\n{command}")


def copy_file_from_device(source, destination):
    command = [adb_path, "pull", source, destination]
    try:
        subprocess.run(command, check=True, shell=True)
        print(f"File copied from {source} to {destination}")
    except subprocess.CalledProcessError as e:
        print(f"Error copying file: {e}\ncommand was:\n{command}")


def copy_file_to_device(source, destination):
    command = [adb_path, "push", source, destination]
    try:
        subprocess.run(command, check=True, shell=True)
        print(f"File copied from {source} to {destination}")
    except subprocess.CalledProcessError as e:
        print(f"Error copying file: {e}\ncommand was:\n{command}")


def delete_file_from_device(source):
    command = [adb_path, "shell", "rm", source]
    try:
        subprocess.run(command, check=True, shell=True)
        print(f"File deleted from {source}")
    except subprocess.CalledProcessError as e:
        print(f"Error deleting file: {e}\ncommand was:\n{command}")


# %%
## Main ##


if __name__ == "__main__":
    source = "/storage/emulated/0/Download/THE PYTHAGOREAN SPIRAL PROJECT.pdf"

    copy_file_from_device(
        source, os.path.join(data_dir, "THE PYTHAGOREAN SPIRAL PROJECT.pdf")
    )


# %%
